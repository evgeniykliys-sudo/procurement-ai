"""ИИ-аналитик (DeepSeek) — единственный модуль, говорящий с LLM.

Сейчас — заглушка с финальными сигнатурами: записка не запрашивается (таск 04 подключит API,
промпт `prompts/analyst.md`, кеш по sha256 входа и вычистку SKU не из входа).
LLM получает только готовые числа и никогда не меняет количества.
"""
from __future__ import annotations

TOP_N = 15


def _short_item(r: dict) -> dict:
    keys = ("sku_id", "category", "supplier_id", "abc", "status", "order_qty", "order_by",
            "stockout_date", "cover_days", "stock_now", "in_transit_qty", "lead_days", "risk", "flags")
    return {k: r.get(k) for k in keys}


def build_summary(plan_json: dict, diff: dict, backtest: dict) -> dict:
    """Компактный вход для LLM: KPI, топ к заказу, топ рисков, изменения, календарь, точность."""
    items = plan_json.get("items", [])
    orders = sorted((r for r in items if r.get("status") == "order_now"), key=lambda r: -(r.get("order_qty") or 0))
    risky = sorted((r for r in items if r.get("risk")),
                   key=lambda r: ("deficit_unavoidable" not in r["risk"], -(r.get("order_qty") or 0)))
    trim = lambda xs: list(xs)[:TOP_N]
    return {
        "as_of": plan_json.get("as_of"),
        "kpi": plan_json.get("kpi", {}),
        "calendar": plan_json.get("calendar", []),
        "top_orders": [_short_item(r) for r in trim(orders)],
        "top_risks": [_short_item(r) for r in trim(risky)],
        "diff": {k: (trim(v) if isinstance(v, list) else v) for k, v in (diff or {}).items()},
        "backtest": {"by_category": (backtest or {}).get("by_category", []),
                     "total": (backtest or {}).get("total", {})},
    }


def analyst_note(summary: dict, cfg, cache_dir) -> dict:
    """Записка аналитика: `{ok, markdown, sku_notes:{sku_id: str}, error, model}`."""
    return {"ok": False, "markdown": "", "sku_notes": {}, "error": "ИИ-слой не подключён", "model": ""}
