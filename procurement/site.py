"""Статический дашборд в `site/`.

Сейчас — минимальная заглушка с финальной сигнатурой: `index.html` с KPI и таблицей «к заказу
сейчас» + `data/*.json`. Таск 03 заменит тело (шаблон, вкладки, ECharts, выгрузки Excel/CSV).
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

KPI_LABELS = [
    ("order_now_positions", "Позиций к заказу сейчас"),
    ("order_now_qty", "Штук к заказу"),
    ("order_now_suppliers", "Поставщиков"),
    ("deficit_unavoidable", "Дефицит неизбежен"),
    ("excess", "Излишки"),
    ("review_price", "На пересмотр цены"),
    ("dead", "Мёртвый ассортимент"),
]


def _fmt(v) -> str:
    if isinstance(v, (int, float)):
        return f"{v:,.0f}".replace(",", " ")
    return escape(str(v)) if v is not None else "—"


def build_site(plan_json: dict, diff: dict, backtest: dict, note: dict, history_index: list,
               out_dir="site") -> Path:
    """Собирает сайт в `out_dir`; возвращает путь к `index.html`."""
    out = Path(out_dir)
    (out / "data").mkdir(parents=True, exist_ok=True)
    for name, data in (("plan", plan_json), ("diff", diff), ("backtest", backtest), ("note", note),
                       ("history", history_index)):
        (out / "data" / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    kpi = plan_json.get("kpi", {})
    tiles = "".join(f"<div class='kpi'><b>{_fmt(kpi.get(k))}</b><span>{escape(label)}</span></div>"
                    for k, label in KPI_LABELS)
    rows = "".join(
        f"<tr><td>{_fmt(r['supplier_id'] or 'поставщик не указан')}</td><td>{_fmt(r['sku_id'])}</td>"
        f"<td class='n'>{_fmt(r['order_qty'])}</td><td>{_fmt(r['order_by'])}</td>"
        f"<td>{_fmt(r['expected_arrival'])}</td></tr>"
        for r in plan_json.get("supplier_orders", []))
    note_html = (escape(note.get("markdown", "")) if note and note.get("ok")
                 else f"ИИ-записка недоступна: {escape((note or {}).get('error') or 'нет данных')}")
    html = f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>План закупок</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#222}}.kpis{{display:flex;gap:12px;flex-wrap:wrap}}
.kpi{{border:1px solid #ddd;border-radius:8px;padding:12px 16px;min-width:150px}}.kpi b{{display:block;font-size:24px}}
table{{border-collapse:collapse;margin-top:16px}}td,th{{border-bottom:1px solid #eee;padding:4px 10px;text-align:left}}
.n{{text-align:right}}</style></head><body>
<h1>План закупок</h1><p>Данные на {_fmt(plan_json.get('as_of'))} · пересчитано {_fmt(plan_json.get('generated_at'))}</p>
<div class="kpis">{tiles}</div>
<p>{note_html}</p>
<h2>К заказу сейчас</h2>
<table><thead><tr><th>Поставщик</th><th>SKU</th><th>Кол-во, шт.</th><th>Разместить до</th><th>Ожидаемый приход</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>
"""
    index = out / "index.html"
    index.write_text(html, encoding="utf-8")
    return index
