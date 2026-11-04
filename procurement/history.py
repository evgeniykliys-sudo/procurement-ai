"""История прогонов (`history/<as_of>.json`) и «что изменилось» с прошлого прогона."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .config import Config

ORDER_STATUS = "order_now"
# В историю — только то, что нужно «что изменилось» и списку прогонов (F5): дата, KPI и по SKU
# статус, количество, риски, категория, поставщик. Без времени запуска — тот же прогон даёт тот же файл.
ITEM_KEYS = ("sku_id", "status", "order_qty", "risk", "category", "supplier_id")
_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")


def _runs(history_dir) -> list[tuple[str, Path]]:
    d = Path(history_dir)
    if not d.is_dir():
        return []
    found = [(m.group(1), p) for p in d.iterdir() if (m := _NAME.match(p.name))]
    return sorted(found)


def save_run(plan_json: dict, history_dir) -> Path:
    """Пишет прогон в `history_dir/<as_of>.json`; та же дата анализа — перезапись.

    Формат: `{as_of, kpi, items:[{sku_id, status, order_qty, risk, category, supplier_id}]}`,
    по SKU на строку, переводы строк LF на любой ОС — повторный прогон даёт побайтно тот же файл."""
    d = Path(history_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{plan_json['as_of']}.json"
    dump = lambda v: json.dumps(v, ensure_ascii=False)
    items = [dump({k: r.get(k) for k in ITEM_KEYS}) for r in plan_json.get("items", [])]
    lines = [f'{{"as_of": {dump(plan_json["as_of"])},', f'"kpi": {dump(plan_json.get("kpi", {}))},',
             '"items": [', ",\n".join(items), "]}", ""]
    path.write_bytes("\n".join(lines).encode("utf-8"))
    return path


def previous_run(history_dir, as_of) -> dict | None:
    """Последний сохранённый прогон с датой анализа раньше `as_of` (date или ГГГГ-ММ-ДД)."""
    key = as_of.isoformat() if isinstance(as_of, date) else str(as_of)
    earlier = [p for d, p in _runs(history_dir) if d < key]
    return json.loads(earlier[-1].read_text(encoding="utf-8")) if earlier else None


def history_index(history_dir) -> list[dict]:
    """Список прогонов по возрастанию даты: `[{as_of, file, kpi}]`."""
    out = []
    for d, p in _runs(history_dir):
        data = json.loads(p.read_text(encoding="utf-8"))
        out.append({"as_of": d, "file": p.name, "kpi": data.get("kpi", {})})
    return out


def diff_runs(cur: dict, prev: dict | None, cfg: Config) -> dict:
    """Что изменилось: новые позиции «заказать сейчас», ушедшие из заказа, изменение количества
    больше `cfg.change_threshold_pct` %, новые риски по SKU.

    `{as_of, prev_as_of, new_orders:[{sku_id, qty, category, supplier_id}],
    dropped_orders:[{sku_id, prev_qty, status}], changed:[{sku_id, prev_qty, qty, pct}],
    new_risks:[{sku_id, risk}]}`; без прошлого прогона списки пусты.
    """
    out = {"as_of": cur.get("as_of"), "prev_as_of": prev.get("as_of") if prev else None,
           "new_orders": [], "dropped_orders": [], "changed": [], "new_risks": []}
    if not prev:
        return out
    now = {r["sku_id"]: r for r in cur.get("items", [])}
    before = {r["sku_id"]: r for r in prev.get("items", [])}
    ordered = lambda r: r is not None and r.get("status") == ORDER_STATUS and (r.get("order_qty") or 0) > 0
    for sid, r in now.items():
        p = before.get(sid)
        if ordered(r) and not ordered(p):
            out["new_orders"].append({"sku_id": sid, "qty": int(r["order_qty"]),
                                      "category": r.get("category"), "supplier_id": r.get("supplier_id")})
        elif ordered(r) and ordered(p):
            q0, q1 = int(p["order_qty"]), int(r["order_qty"])
            pct = round((q1 - q0) / q0 * 100, 1)
            if abs(pct) > cfg.change_threshold_pct:
                out["changed"].append({"sku_id": sid, "prev_qty": q0, "qty": q1, "pct": pct})
        old_risks = set(p.get("risk", [])) if p else set()
        for risk in r.get("risk", []):
            if risk not in old_risks:
                out["new_risks"].append({"sku_id": sid, "risk": risk})
    for sid, p in before.items():
        r = now.get(sid)
        if ordered(p) and not ordered(r):
            out["dropped_orders"].append({"sku_id": sid, "prev_qty": int(p["order_qty"]),
                                          "status": r.get("status") if r else None})
    out["new_orders"].sort(key=lambda x: -x["qty"])
    out["changed"].sort(key=lambda x: -abs(x["pct"]))
    out["dropped_orders"].sort(key=lambda x: -x["prev_qty"])
    return out
