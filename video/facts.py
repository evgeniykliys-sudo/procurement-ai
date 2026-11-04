"""Числа для сценария — из свежего прогона (out/*.json, .autopilot/state.js), а не из головы.

Текст сцен в scenes.json содержит подстановки вида {order_now_qty}; tts.py и record.py
заполняют их отсюда. Если расчёт поменялся — перезапусти `run --no-ai` и пересобери ролик.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def num(n) -> str:
    """43066 → «43 066» (обычный пробел; для озвучки tts.py его убирает)."""
    return f"{round(n):,}".replace(",", " ")


def _pct(x) -> str:
    return f"{round(x * 100)}"


def _load(name: str):
    return json.loads((ROOT / "out" / name).read_text(encoding="utf-8"))


def _state() -> dict:
    s = (ROOT / ".autopilot" / "state.js").read_text(encoding="utf-8")
    return json.loads(s[s.index("{"):].rstrip().rstrip(";"))


def facts() -> dict:
    plan = _load("plan.json")
    k = plan["kpi"]
    bt = _load("backtest.json")
    diff = _load("diff.json")
    st = _state()
    as_of = date.fromisoformat(plan["as_of"][:10])
    cats = len({r["category"] for r in plan["items"]})
    f: dict = {
        "as_of_words": f"{as_of.day} {MONTHS_GEN[as_of.month - 1]}",
        "as_of_year": as_of.year,
        "skus_total": num(k["skus_total"]),
        "categories": cats,
        "order_now_positions": num(k["order_now_positions"]),
        "order_now_qty": num(k["order_now_qty"]),
        "order_now_suppliers": num(k["order_now_suppliers"]),
        "order_later_positions": num(k["order_later_positions"]),
        "deficit_unavoidable": num(k["deficit_unavoidable"]),
        "deficit_if_late": num(k["deficit_if_late"]),
        "excess": num(k["excess"]),
        "late_lines": num(k["late_lines"]),
        "low_reliability_suppliers": num(k["low_reliability_suppliers"]),
        "review_price": num(k["review_price"]),
        "review_low_markup": num(k["review_low_markup"]),
        "review_no_cost": num(k["review_no_cost"]),
        "dead": num(k["dead"]),
        "dead_sell": num(k["dead_sell"]),
        "dead_close": num(k["dead_close"]),
        "in_transit_future_qty": num(k["in_transit_future_qty"]),
        # главная метрика дашборда — ошибка по SKU (D10); по сумме категории — только для сведения
        "wape_model": _pct(bt["total"]["wape_sku_model"]),
        "wape_naive": _pct(bt["total"]["wape_sku_naive"]),
        "bt_better": sum(1 for c in bt["by_category"] if c.get("wape_sku_model") is not None
                         and c.get("wape_sku_naive") is not None and c["wape_sku_model"] < c["wape_sku_naive"]),
        "bt_cats": len(bt["by_category"]),
        "diff_prev": diff.get("prev_as_of") or "",
        "diff_new_orders": num(diff.get("new_orders_count", len(diff.get("new_orders", [])))),
        "diff_dropped": num(diff.get("dropped_orders_count", len(diff.get("dropped_orders", [])))),
        "diff_new_risks": num(diff.get("new_risks_count", len(diff.get("new_risks", [])))),
        "req_total": st["requirements"]["total"],
        "req_done": st["requirements"]["done"],
        "tickets": len(st["tickets"]),
        "tests_passed": (st.get("tests") or {}).get("passed", 0),
        "config_params": len(plan.get("config", {})),
    }
    # слова, согласованные с числом
    f["w_positions"] = plural(k["order_now_positions"], "позицию", "позиции", "позиций")
    f["w_qty"] = plural(k["order_now_qty"], "штуку", "штуки", "штук")
    f["w_suppliers"] = plural(k["order_now_suppliers"], "поставщика", "поставщиков", "поставщиков")
    f["w_deficit"] = plural(k["deficit_unavoidable"], "позиция", "позиции", "позиций")
    return f


def render(text: str, fx: dict | None = None) -> str:
    return text.format_map(fx if fx is not None else facts())


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for key, v in facts().items():
        print(f"{key} = {v}")
