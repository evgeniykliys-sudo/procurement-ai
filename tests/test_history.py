"""История прогонов: запись по дате анализа, предыдущий прогон, «что изменилось»."""
import json

from procurement.config import Config
from procurement.history import diff_runs, history_index, previous_run, save_run


def item(sku_id, status="order_now", qty=100, risk=()):
    return {"sku_id": sku_id, "status": status, "order_qty": qty, "risk": list(risk),
            "category": "cat_a", "supplier_id": "SUP-1"}


def run(as_of, items, **kpi):
    return {"as_of": as_of, "kpi": kpi, "items": items, "series": {"X": {"history": [[1, 2]]}}}


def test_save_overwrites_same_date_and_finds_previous(tmp_path):
    p1 = save_run(run("2026-07-01", [item("A")], order_now_positions=1), tmp_path)
    assert p1.name == "2026-07-01.json"
    save_run(run("2026-08-13", [item("A", qty=1)]), tmp_path)
    p3 = save_run(run("2026-08-13", [item("A", qty=2)]), tmp_path)  # повтор той же даты
    assert sorted(f.name for f in tmp_path.iterdir()) == ["2026-07-01.json", "2026-08-13.json"]
    assert json.loads(p3.read_text(encoding="utf-8"))["items"][0]["order_qty"] == 2
    assert "series" not in json.loads(p1.read_text(encoding="utf-8"))  # тяжёлые ряды не в истории
    prev = previous_run(tmp_path, "2026-08-13")
    assert prev["as_of"] == "2026-07-01"
    assert previous_run(tmp_path, "2026-07-01") is None
    idx = history_index(tmp_path)
    assert [r["as_of"] for r in idx] == ["2026-07-01", "2026-08-13"]
    assert idx[0]["kpi"]["order_now_positions"] == 1


def test_saved_run_is_compact_and_byte_identical_on_rerun(tmp_path):
    def full(generated_at):
        it = {**item("A", risk=["excess"]), "explain": {"gross": 1.5}, "flags": ["x"], "cover_days": 10}
        return {"as_of": "2026-08-13", "generated_at": generated_at, "kpi": {"order_now_positions": 1},
                "items": [it], "supplier_orders": [{"sku_id": "A"}], "calendar": [], "config": {"a": 1},
                "series": {"A": {}}}
    p1 = save_run(full("2026-11-04T10:00:00"), tmp_path / "one")
    p2 = save_run(full("2026-11-05T07:00:00"), tmp_path / "two")
    assert p1.read_bytes() == p2.read_bytes()  # время запуска в историю не идёт
    data = json.loads(p1.read_text(encoding="utf-8"))
    assert set(data) == {"as_of", "kpi", "items"}
    assert data["items"] == [{"sku_id": "A", "status": "order_now", "order_qty": 100, "risk": ["excess"],
                              "category": "cat_a", "supplier_id": "SUP-1"}]
    # «что изменилось» работает на сжатом формате
    prev = previous_run(tmp_path / "one", "2026-09-01")
    d = diff_runs(run("2026-09-01", [item("A", qty=200, risk=["excess", "late_po"])]), prev, Config())
    assert [c["sku_id"] for c in d["changed"]] == ["A"]
    assert d["new_risks"] == [{"sku_id": "A", "risk": "late_po"}]


def test_diff_finds_new_dropped_changed_and_risks():
    prev = run("2026-07-01", [item("KEEP", qty=100), item("SMALL", qty=100), item("GONE", qty=50),
                              item("LATER", status="order_later", qty=10),
                              item("R", status="ok", qty=0, risk=["excess"])])
    cur = run("2026-08-13", [item("KEEP", qty=130), item("SMALL", qty=115), item("NEW", qty=40),
                             item("GONE", status="ok", qty=0),
                             item("LATER", qty=10, risk=["deficit_if_late"]),
                             item("R", status="ok", qty=0, risk=["excess", "deficit_unavoidable"])])
    d = diff_runs(cur, prev, Config())
    assert d["prev_as_of"] == "2026-07-01" and d["as_of"] == "2026-08-13"
    assert {r["sku_id"] for r in d["new_orders"]} == {"NEW", "LATER"}
    assert [r["sku_id"] for r in d["dropped_orders"]] == ["GONE"]
    assert d["dropped_orders"][0]["status"] == "ok"
    ch = {r["sku_id"]: r for r in d["changed"]}
    assert set(ch) == {"KEEP"}  # +30% > 20%, +15% — нет
    assert (ch["KEEP"]["prev_qty"], ch["KEEP"]["qty"], ch["KEEP"]["pct"]) == (100, 130, 30.0)
    assert {(r["sku_id"], r["risk"]) for r in d["new_risks"]} == {
        ("LATER", "deficit_if_late"), ("R", "deficit_unavoidable")}
    assert diff_runs(cur, prev, Config(change_threshold_pct=10))["changed"][1]["sku_id"] == "SMALL"


def test_diff_without_previous_run_is_empty():
    d = diff_runs(run("2026-08-13", [item("A")]), None, Config())
    assert d["prev_as_of"] is None
    assert d["new_orders"] == [] and d["changed"] == [] and d["new_risks"] == []
