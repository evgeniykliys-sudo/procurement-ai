"""CLI: `python -m procurement run [...]`, `seed-history` и `notify` (дайджест по готовому out/)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

from . import ai, notify, site
from .backtest import default_cut, mark_weak_forecast, run_backtest
from .config import load_config
from .data import DataError, cut_dataset, detect_as_of, load_dataset
from .demand import build_demand
from .history import diff_runs, history_index, previous_run, save_run
from .plan import build_plan, plan_to_json

SEED_AS_OF = date(2026, 7, 1)  # первый «прошлый» прогон (спецификация, история 32)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def compute_plan(raw, cfg, as_of: date | None = None) -> tuple[dict, object, object]:
    """Загрузка → дата анализа → (обрезка, если дата раньше данных) → прогноз → план.
    Возвращает (plan_json, полный набор, конфиг)."""
    ds_full = load_dataset(raw)
    data_as_of = detect_as_of(ds_full, replace(cfg, as_of=None))
    as_of = as_of or cfg.as_of or data_as_of
    if as_of > data_as_of:  # остатка и продаж на эту дату в выгрузке нет — расчёт был бы на нулях
        raise ValueError(f"дата анализа {as_of.isoformat()} позже данных выгрузки ({data_as_of.isoformat()}): "
                         "уберите as_of из config.toml или укажите дату не позже данных")
    ds = cut_dataset(ds_full, as_of, cfg.partial_day) if as_of < data_as_of else ds_full
    demand = build_demand(ds, cfg, as_of)
    plan = build_plan(ds, demand, cfg, as_of)
    return plan_to_json(plan), ds_full, cfg


def cmd_run(args) -> int:
    t0 = time.time()
    cfg = load_config(args.config)
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    plan_json, ds_full, cfg = compute_plan(args.raw, cfg, as_of)
    # бэктест — от даты прогона, а не от конца выгрузки: при --as-of в прошлое он не видит данных
    # после неё (иначе флаг «прогноз не точнее наивного» и записка ИИ опирались бы на будущее)
    backtest = run_backtest(ds_full, cfg, cut=default_cut(ds_full, cfg, date.fromisoformat(plan_json["as_of"])))
    mark_weak_forecast(plan_json, backtest)
    hist_dir, out_dir = Path(args.history), Path(args.out)
    prev = previous_run(hist_dir, plan_json["as_of"])
    diff = diff_runs(plan_json, prev, cfg)
    save_run(plan_json, hist_dir)
    if args.no_ai:
        note = {"ok": False, "markdown": "", "sku_notes": {}, "error": "ИИ-записка отключена (--no-ai)",
                "model": ""}
    else:
        note = ai.analyst_note(ai.build_summary(plan_json, diff, backtest), cfg, Path(".cache") / "ai")
    _write(out_dir / "plan.json", plan_json)
    _write(out_dir / "backtest.json", backtest)
    _write(out_dir / "diff.json", diff)
    _write(out_dir / "note.json", note)
    site.build_site(plan_json, diff, backtest, note, history_index(hist_dir), out_dir=args.site)
    sent = notify.send_digest(plan_json, diff, cfg.site_url) if args.notify else False
    k = plan_json["kpi"]
    print(f"Данные на {plan_json['as_of']}: к заказу сейчас {k['order_now_positions']} поз. / "
          f"{k['order_now_qty']} шт. у {k['order_now_suppliers']} поставщиков; дефицит неизбежен — "
          f"{k['deficit_unavoidable']}, излишки — {k['excess']}, на пересмотр цены — {k['review_price']}, "
          f"мёртвых — {k['dead']}.")
    print(f"Сравнение с прогоном {diff['prev_as_of'] or '— (нет прошлого)'}; "
          f"записка ИИ: {'есть' if note.get('ok') else 'нет'}; дайджест: {'отправлен' if sent else 'нет'}; "
          f"{time.time() - t0:.1f} с.")
    return 0


def cmd_notify(args) -> int:
    """Дайджест по уже посчитанному прогону (out/plan.json, out/diff.json) — в Actions после деплоя
    сайта, чтобы ссылка вела на обновлённый дашборд. Без секретов Telegram — пропуск, не ошибка."""
    cfg = load_config(args.config)
    out_dir = Path(args.out)
    plan_path = out_dir / "plan.json"
    if not plan_path.exists():
        raise ValueError(f"нет {plan_path.as_posix()} — сначала `python -m procurement run`")
    plan_json = json.loads(plan_path.read_text(encoding="utf-8"))
    diff_path = out_dir / "diff.json"
    diff = json.loads(diff_path.read_text(encoding="utf-8")) if diff_path.exists() else {}
    if not (ai.read_secret("TELEGRAM_BOT_TOKEN") and ai.read_secret("TELEGRAM_CHAT_ID")):
        notify.send_digest(plan_json, diff, cfg.site_url)  # пишет в лог, каких переменных нет
        print("Дайджест: не отправлен — секреты Telegram не заданы.")
        return 0
    sent = notify.send_digest(plan_json, diff, cfg.site_url)
    print(f"Дайджест по данным на {plan_json.get('as_of')}: {'отправлен' if sent else 'НЕ отправлен'}.")
    return 0 if sent else 1


def cmd_seed_history(args) -> int:
    cfg = load_config(args.config)
    plan_json, _, _ = compute_plan(args.raw, cfg, SEED_AS_OF)
    path = save_run(plan_json, Path(args.history))
    print(f"Прошлый прогон на {plan_json['as_of']} записан: {path}")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # вывод в трубу/файл — UTF-8, а не кодовая страница Windows
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(prog="python -m procurement", description="Планирование закупок")
    sub = p.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--raw", default="data/raw", help="папка с CSV-выгрузкой ERP")
    common.add_argument("--config", default="config.toml", help="файл параметров")
    common.add_argument("--history", default="history", help="папка истории прогонов")
    r = sub.add_parser("run", parents=[common], help="полный прогон: план, бэктест, история, сайт")
    r.add_argument("--as-of", help="дата анализа ГГГГ-ММ-ДД (по умолчанию — по данным)")
    r.add_argument("--no-ai", action="store_true", help="без записки ИИ-аналитика")
    r.add_argument("--notify", action="store_true", help="отправить дайджест в Telegram")
    r.add_argument("--out", default="out", help="папка результатов расчёта")
    r.add_argument("--site", default="site", help="папка сайта")
    r.set_defaults(func=cmd_run)
    s = sub.add_parser("seed-history", parents=[common], help=f"прошлый прогон на {SEED_AS_OF}")
    s.set_defaults(func=cmd_seed_history)
    n = sub.add_parser("notify", help="дайджест в Telegram по готовому прогону из --out")
    n.add_argument("--config", default="config.toml", help="файл параметров")
    n.add_argument("--out", default="out", help="папка результатов расчёта (plan.json, diff.json)")
    n.set_defaults(func=cmd_notify)
    args = p.parse_args(argv)
    try:
        return args.func(args)
    except DataError as exc:
        print(f"Ошибка входных данных: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
