"""ИИ-аналитик (DeepSeek) — единственный модуль, говорящий с LLM.

LLM получает компактную сводку уже посчитанного расчёта (`build_summary`) и пишет по-русски
записку и пояснения к рисковым SKU (`analyst_note`). Количества LLM не меняет: ответ — только
текст. SKU, которых нет во входе, вычищаются; ответ кешируется по sha256 входа.
Ключ — только из `DEEPSEEK_API_KEY` (окружение или локальный `.env`), его значение нигде
не печатается.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

import requests

from .labels import CATEGORY_LABELS, RISK_LABELS, STATUS_LABELS

log = logging.getLogger(__name__)

TOP_N = 15
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "analyst.md"
ENV_FILE = ".env"
SKU_RE = re.compile(r"\bSKU-[0-9A-Za-z]+")

# Порядок важности рисков и сколько мест в топ-15 у каждого (остаток добирается по порядку).
RISK_ORDER = ("deficit_unavoidable", "deficit_if_late", "late_po", "excess", "low_reliability")
RISK_QUOTA = {"deficit_unavoidable": 7, "deficit_if_late": 3, "late_po": 2, "excess": 3}
ABC_ORDER = {"A": 0, "B": 1, "C": 2}

ITEM_KEYS = ("sku_id", "category", "supplier_id", "abc", "status", "order_qty", "order_by",
             "stockout_date", "cover_days", "stock_now", "in_transit_qty", "next_eta", "lead_days",
             "markup_12m", "sales_12m", "forecast_12m", "risk", "flags")
EXPLAIN_KEYS = ("forecast_horizon", "safety", "stock_at_arrival", "transit", "gross", "uplift", "lot",
                "lot_source", "cap", "merged_until_review", "final", "stock", "transit_lead", "forecast_lead",
                "arrival", "at", "days_short", "lost_lead", "excess_qty")
ORDER_KEYS = ("sku_id", "category", "supplier_id", "abc", "order_qty", "order_by", "stockout_date",
              "cover_days", "risk")
DIFF_N = 10
# Точность — в процентах, как на дашборде; главная метрика — по SKU (D10), по сумме категории — для сведения.
BACKTEST_KEYS = ("wape_sku_model", "wape_sku_naive", "wape_model", "wape_naive", "bias_model", "bias_naive")


def _pct(v) -> int | None:
    """Доля → целые проценты тем же округлением, что у дашборда (`site.fmt_pct`): 0.656 → 66."""
    return None if v is None else int(f"{float(v) * 100:.0f}")


def _ru(row: dict) -> dict:
    """Коды категории, статуса и рисков → русские подписи дашборда: LLM переписывает их в текст как есть."""
    out = dict(row)
    if "category" in out:
        out["category"] = CATEGORY_LABELS.get(out["category"], out["category"])
    if "status" in out:
        out["status"] = STATUS_LABELS.get(out["status"], out["status"])
    if isinstance(out.get("risk"), list):
        out["risk"] = [RISK_LABELS.get(x, x) for x in out["risk"]]
    elif isinstance(out.get("risk"), str):
        out["risk"] = RISK_LABELS.get(out["risk"], out["risk"])
    return out


def read_secret(name: str) -> str:
    """Значение переменной окружения; нет — строка из локального `.env` (текущая папка); нет — ""."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    path = Path(ENV_FILE)
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, val = line.partition("=")
        if sep and key.strip() == name:
            return val.strip().strip("'\"")
    return ""


def _short_item(r: dict, with_explain: bool = False) -> dict:
    out = {k: r.get(k) for k in (ITEM_KEYS if with_explain else ORDER_KEYS)}
    if with_explain:
        ex = r.get("explain") or {}
        out["explain"] = {k: ex[k] for k in EXPLAIN_KEYS if ex.get(k) is not None}
        if ex.get("lot_source") != "потолок":
            out["explain"].pop("cap", None)  # потолок нужен только там, где он сработал
    return _ru(out)


def _primary_risk(r: dict) -> int:
    return next((i for i, k in enumerate(RISK_ORDER) if k in r["risk"]), len(RISK_ORDER))


def _risk_key(r: dict) -> tuple:
    kind = _primary_risk(r)
    if kind < len(RISK_ORDER) and RISK_ORDER[kind] == "excess":
        weight = (r.get("explain") or {}).get("excess_qty") or 0
    else:
        weight = r.get("order_qty") or 0
    return kind, ABC_ORDER.get(r.get("abc"), 3), -weight, r["sku_id"]


def _top_risks(items: list[dict]) -> list[dict]:
    """Топ-15 рисковых SKU: сначала квоты по видам риска, затем добор по важности."""
    risky = sorted((r for r in items if r.get("risk")), key=_risk_key)
    picked: dict[str, dict] = {}
    for kind, quota in RISK_QUOTA.items():
        for r in [r for r in risky if _primary_risk(r) == RISK_ORDER.index(kind)][:quota]:
            picked[r["sku_id"]] = r
    for r in risky:
        if len(picked) >= TOP_N:
            break
        picked.setdefault(r["sku_id"], r)
    return sorted(picked.values(), key=_risk_key)[:TOP_N]


def build_summary(plan_json: dict, diff: dict, backtest: dict) -> dict:
    """Компактный вход для LLM (≤ ~8 тыс. токенов): KPI, топ к заказу, топ рисков с расшифровкой,
    изменения с прошлого прогона, календарь, просроченные заказы, надёжность, точность прогноза.

    Категории, статусы и риски — русскими подписями дашборда, доли — целыми процентами, как на
    дашборде: LLM переписывает их как есть, и записка не расходится со страницей."""
    items = plan_json.get("items", [])
    orders = sorted((r for r in items if r.get("status") == "order_now"),
                    key=lambda r: (-(r.get("order_qty") or 0), r["sku_id"]))
    diff = diff or {}
    diff_out = {"prev_as_of": diff.get("prev_as_of")}
    for key in ("new_orders", "dropped_orders", "changed", "new_risks"):
        rows = list(diff.get(key) or [])
        diff_out[f"{key}_count"] = len(rows)
        diff_out[key] = [_ru(r) for r in rows[:DIFF_N]]
    # сначала самый сильный недовоз (меньшая доля привезённого)
    weak = sorted((r for r in plan_json.get("reliability", []) if (r.get("uplift") or 1) > 1),
                  key=lambda r: r.get("fill_ratio") or 0)
    bt = backtest or {}
    return {
        "as_of": plan_json.get("as_of"),
        "kpi": plan_json.get("kpi", {}),
        "calendar": plan_json.get("calendar", []),
        "top_orders": [_short_item(r) for r in orders[:TOP_N]],
        "top_risks": [_short_item(r, with_explain=True) for r in _top_risks(items)],
        "diff": diff_out,
        "stale_po": list(plan_json.get("stale_po", []))[:5],
        "low_reliability": [{"supplier_id": r.get("supplier_id"), "fill_pct": _pct(r.get("fill_ratio") or 0),
                             "orders_n": r.get("orders_n")} for r in weak[:5]],
        "backtest": {"by_category": [{"category": CATEGORY_LABELS.get(c.get("category"), c.get("category")),
                                      **{k: _pct(c.get(k)) for k in BACKTEST_KEYS}}
                                     for c in bt.get("by_category", [])],
                     "total": {k: _pct((bt.get("total") or {}).get(k)) for k in BACKTEST_KEYS}},
    }


def _fail(error: str, model: str) -> dict:
    log.warning("ИИ-записка недоступна: %s", error)
    return {"ok": False, "markdown": "", "sku_notes": {}, "error": error, "model": model}


def _clean(markdown: str, notes: dict, allowed: set[str]) -> tuple[str, dict]:
    """Строки записки с SKU не из входа выбрасываются; пояснения — только к SKU из входа."""
    lines = markdown.splitlines()
    kept = [ln for ln in lines if set(SKU_RE.findall(ln)) <= allowed]
    clean_notes = {k: str(v).strip() for k, v in notes.items()
                   if k in allowed and isinstance(v, str) and v.strip()}
    if len(kept) < len(lines) or len(clean_notes) < len(notes):
        log.info("Из ответа ИИ вычищено строк: %d, пояснений: %d (SKU не из входа).",
                 len(lines) - len(kept), len(notes) - len(clean_notes))
    return "\n".join(kept).strip(), clean_notes


def _parse(content: str) -> dict | None:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def analyst_note(summary: dict, cfg, cache_dir) -> dict:
    """Записка аналитика: `{ok, markdown, sku_notes:{sku_id: str}, error, model}`.

    Без ключа, при ошибке API, таймауте или негодном ответе — `ok: False` и причина по-русски.
    """
    model = cfg.llm_model
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    user = json.dumps(summary, ensure_ascii=False, separators=(",", ":"), default=str)
    digest = hashlib.sha256(json.dumps({"model": model, "prompt": prompt, "input": user},
                                       ensure_ascii=False).encode("utf-8")).hexdigest()
    cache_file = Path(cache_dir) / f"{digest}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except ValueError:
            pass  # битый кеш — запросим заново

    key = read_secret("DEEPSEEK_API_KEY")
    if not key:
        return _fail("не задан ключ DEEPSEEK_API_KEY", model)
    url = cfg.llm_base_url.rstrip("/") + "/chat/completions"
    body = {"model": model, "temperature": 0.3, "max_tokens": 4000,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": user}]}
    try:
        resp = requests.post(url, json=body, timeout=cfg.llm_timeout_s,
                             headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    except requests.Timeout:
        return _fail(f"DeepSeek не ответил за {cfg.llm_timeout_s} с", model)
    except requests.ConnectionError:
        return _fail("нет связи с DeepSeek", model)
    except requests.RequestException as exc:  # текст исключения не выводим: в нём может быть заголовок
        return _fail(f"ошибка запроса к DeepSeek ({type(exc).__name__})", model)
    if resp.status_code >= 400:
        hint = {401: " — проверьте ключ", 402: " — закончился баланс", 429: " — превышен лимит запросов"}
        return _fail(f"DeepSeek вернул ошибку HTTP {resp.status_code}{hint.get(resp.status_code, '')}", model)
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        return _fail("ответ DeepSeek не в ожидаемом формате", model)
    data = _parse(content or "")
    if data is None:
        return _fail("ответ ИИ не в формате JSON", model)
    markdown, notes = data.get("markdown"), data.get("sku_notes") or {}
    if not isinstance(markdown, str) or not markdown.strip():
        return _fail("в ответе ИИ нет записки (поле markdown)", model)
    allowed = set(SKU_RE.findall(user))
    markdown, notes = _clean(markdown, notes if isinstance(notes, dict) else {}, allowed)
    note = {"ok": True, "markdown": markdown, "sku_notes": notes, "error": None, "model": model}
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(note, ensure_ascii=False, indent=1), encoding="utf-8")
    return note
