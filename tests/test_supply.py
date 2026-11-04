from datetime import date, timedelta

import pytest

from procurement.config import Config
from procurement.supply import in_transit, lead_times, lot_size, supplier_reliability

AS_OF = date(2026, 8, 13)


def po(sku, lead=120, supplier="SUP-1", status="closed", order="2025-01-10", promised="2025-05-10",
       ordered=100, received=100, pending=0, po_id=None):
    return {"po_id": po_id or f"PO-{sku}-{lead}-{order}", "sku_id": sku, "supplier_id": supplier,
            "order_date": order, "promised_date": promised, "planned_lead_days": lead, "status": status,
            "qty_ordered": ordered, "qty_confirmed": 0, "qty_received": received, "qty_pending": pending}


# --- плечо -------------------------------------------------------------------

@pytest.fixture
def lead_ds(make_dataset):
    skus = [{"sku_id": "OWN", "supplier_id": "SUP-1"}, {"sku_id": "SUP", "supplier_id": "SUP-1"},
            {"sku_id": "NONE", "supplier_id": "SUP-9"}, {"sku_id": "LONG", "supplier_id": "SUP-2"},
            {"sku_id": "SHORT", "supplier_id": "SUP-2"}, {"sku_id": "DIM", "supplier_id": "SUP-3"},
            {"sku_id": "NOSUP", "supplier_id": None, "supply_type": "unknown"}]
    pos = [po("OWN", 100), po("OWN", 140), po("OWN", -5), po("OWN", 0),
           po("X1", 60), po("X1", 80), po("LONG", 444, "SUP-2"), po("SHORT", 10, "SUP-2")]
    suppliers = [{"supplier_id": "SUP-1"}, {"supplier_id": "SUP-2"}, {"supplier_id": "SUP-9"},
                 {"supplier_id": "SUP-3", "median_planned_lead_days": 95}]
    return make_dataset(skus=skus, po=pos, suppliers=suppliers)


def test_lead_times_chain_sku_supplier_default(lead_ds):
    lt = lead_times(lead_ds, Config()).set_index("sku_id")
    assert (lt.loc["OWN", "lead_days"], lt.loc["OWN", "lead_source"]) == (120, "sku")  # ≤ 0 отброшены
    # медиана поставщика SUP-1 по его заказам с плечом > 0: 60, 80, 100, 140 → 90
    assert (lt.loc["SUP", "lead_days"], lt.loc["SUP", "lead_source"]) == (90, "supplier")
    assert (lt.loc["DIM", "lead_days"], lt.loc["DIM", "lead_source"]) == (95, "supplier")
    assert (lt.loc["NONE", "lead_days"], lt.loc["NONE", "lead_source"]) == (123, "default")
    assert (lt.loc["NOSUP", "lead_days"], lt.loc["NOSUP", "lead_source"]) == (123, "default")
    assert lt.loc["LONG", "lead_days"] == 180 and lt.loc["SHORT", "lead_days"] == 30
    assert len(lt) == 7


def test_lead_bounds_come_from_config(lead_ds):
    lt = lead_times(lead_ds, Config(lead_max_days=150, lead_default_days=100)).set_index("sku_id")
    assert lt.loc["LONG", "lead_days"] == 150
    assert lt.loc["NONE", "lead_days"] == 100


def test_lead_times_real_data_in_bounds(real_ds):
    lt = lead_times(real_ds, Config())
    assert len(lt) == 791
    assert lt["lead_days"].between(30, 180).all()
    assert set(lt["lead_source"]) <= {"sku", "supplier", "default"}


# --- в пути ------------------------------------------------------------------

def test_in_transit_synthetic(make_dataset):
    d = lambda n: (AS_OF + timedelta(days=n)).isoformat()
    pos = [
        po("A", status="open", order="2026-04-01", promised=d(20), pending=300, received=0, po_id="P1"),
        po("A", status="open", order="2026-03-01", promised=d(-20), pending=100, received=50, po_id="P2"),
        po("B", status="open", order="2025-12-01", promised=d(-120), pending=70, received=0, po_id="P3"),
        po("B", status="open", order="2026-01-01", promised=d(10), pending=0, received=200, po_id="P4"),
        po("C", status="closed", order="2026-01-01", promised=d(10), pending=500, received=0, po_id="P5"),
    ]
    arrivals, stale = in_transit(make_dataset(po=pos), Config(), AS_OF)
    arr = arrivals.set_index("po_id")
    assert set(arr.index) == {"P1", "P2"}
    assert (arr.loc["P1", "eta"], arr.loc["P1", "qty"], bool(arr.loc["P1", "late"])) == (AS_OF + timedelta(days=20), 300, False)
    assert (arr.loc["P2", "eta"], arr.loc["P2", "qty"], bool(arr.loc["P2", "late"])) == (AS_OF + timedelta(days=30), 100, True)
    assert list(stale["po_id"]) == ["P3"]
    assert stale.iloc[0]["days_overdue"] == 120 and stale.iloc[0]["qty_pending"] == 70


def test_in_transit_real_data(real_ds):
    arrivals, stale = in_transit(real_ds, Config(), AS_OF)
    on_time = arrivals[~arrivals["late"]]
    assert len(on_time) == 22 and on_time["qty"].sum() == 17127
    assert (on_time["eta"] >= AS_OF).all()
    late = arrivals[arrivals["late"]]
    assert len(late) == 5 and (late["eta"] == AS_OF + timedelta(days=30)).all()
    assert len(stale) == 4 and (stale["days_overdue"] > 90).all()
    assert len(late) + len(stale) == 9


# --- надёжность --------------------------------------------------------------

def test_supplier_reliability_and_uplift(make_dataset):
    pos = [po("A", supplier="S-GOOD", ordered=100, received=98),
           po("A", supplier="S-MID", ordered=100, received=80),
           po("A", supplier="S-MID", ordered=100, received=80, status="completed"),
           po("Y", supplier="S-MID", ordered=100, received=0, order="2026-06-01"),  # моложе 180 дн.
           po("O", supplier="S-MID", ordered=100, received=0, status="open"),       # не закрыт
           po("B", supplier="S-BAD", ordered=100, received=30)]
    # «получено» — по приходам receipts.csv в окне заказа (D01), поле qty_received не важно
    receipts = [rec("G", "2025-05-01", 98, "S-GOOD"), rec("M", "2025-05-01", 160, "S-MID"),
                rec("Y", "2026-06-10", 100, "S-MID"), rec("O", "2025-05-01", 100, "S-MID"),
                rec("B", "2025-05-01", 30, "S-BAD")]
    pos[0]["sku_id"] = "G"
    pos[1]["sku_id"] = pos[2]["sku_id"] = "M"
    suppliers = [{"supplier_id": s} for s in ("S-GOOD", "S-MID", "S-BAD", "S-NEW")]
    ds = make_dataset(po=pos, receipts=receipts, suppliers=suppliers)
    rel = supplier_reliability(ds, Config(reliability_max_uplift=1.5), AS_OF).set_index("supplier_id")
    assert rel.loc["S-GOOD", "uplift"] == 1.0
    assert rel.loc["S-MID", "fill_ratio"] == pytest.approx(0.8) and rel.loc["S-MID", "orders_n"] == 2
    assert rel.loc["S-MID", "uplift"] == pytest.approx(1.25)
    assert rel.loc["S-BAD", "uplift"] == pytest.approx(1.5)  # 1/0.3 = 3.3 → потолок
    assert rel.loc["S-NEW", "uplift"] == 1.0 and rel.loc["S-NEW", "orders_n"] == 0
    # потолок по умолчанию ×1.2 (config.toml)
    default = supplier_reliability(ds, Config(), AS_OF).set_index("supplier_id")
    assert default.loc["S-MID", "uplift"] == pytest.approx(1.2)
    assert default.loc["S-BAD", "uplift"] == pytest.approx(1.2)


def test_supplier_reliability_real_uplift_bounds(real_ds):
    rel = supplier_reliability(real_ds, Config(), AS_OF)
    assert rel["uplift"].between(1.0, 1.2).all()
    assert set(real_ds.suppliers["supplier_id"]) <= set(rel["supplier_id"])
    assert (rel["orders_n"] > 0).any()


# --- кратность ---------------------------------------------------------------

def test_lot_size_rounds_median_receipt(make_dataset):
    rec = []
    for sku, qtys in {"A": [37, 40, 45], "B": [120, 130, 125], "C": [1430], "D": [3, 2]}.items():
        rec += [{"sku_id": sku, "receipt_date": "2025-01-0" + str(i + 1), "qty": q} for i, q in enumerate(qtys)]
    skus = [{"sku_id": s} for s in "ABCDE"]
    lots = lot_size(make_dataset(skus=skus, receipts=rec))
    assert lots.to_dict() == {"A": 40, "B": 150, "C": 1400, "D": 10, "E": 1}


# --- надёжность по приходам (D01) ------------------------------------------

def rec(sku, day, qty, supplier="S1"):
    return {"sku_id": sku, "receipt_date": day, "qty": qty, "supplier_id": supplier}


def rel_of(make_dataset, pos, receipts, sup="S1", cfg=None):
    ds = make_dataset(po=pos, receipts=receipts, suppliers=[{"supplier_id": sup}])
    return supplier_reliability(ds, cfg or Config(), AS_OF).set_index("supplier_id").loc[sup]


def test_reliability_counts_receipts_in_window_not_qty_received(make_dataset):
    pos = [po("A", supplier="S1", order="2025-01-10", promised="2025-05-10", ordered=100, received=0, pending=100)]
    r = rel_of(make_dataset, pos, [rec("A", "2025-07-01", 100)])  # в окне
    assert r["fill_ratio"] == pytest.approx(1.0) and r["uplift"] == 1.0


def test_reliability_ignores_receipts_outside_window_or_other_supplier(make_dataset):
    pos = [po("A", supplier="S1", order="2025-01-10", promised="2025-05-10", ordered=100, received=100)]
    receipts = [rec("A", "2025-01-05", 40),             # до заказа
                rec("A", "2025-08-09", 40),             # promised + 91 дн.
                rec("A", "2025-03-01", 40, "S2"),       # другой поставщик
                rec("B", "2025-03-01", 40),             # другой SKU
                rec("A", "2025-08-08", 30)]             # promised + 90
    # окно — из config: при 90 дн. приход на +91 не засчитан
    r = rel_of(make_dataset, pos, receipts, cfg=Config(reliability_receipt_window_days=90))
    assert r["fill_ratio"] == pytest.approx(0.3)
    assert r["uplift"] == pytest.approx(1.2)
    # по умолчанию окно 180 дн.: +91 засчитан
    assert rel_of(make_dataset, pos, receipts)["fill_ratio"] == pytest.approx(0.7)
    assert rel_of(make_dataset, pos, [rec("A", "2025-11-07", 50)])["fill_ratio"] == pytest.approx(0.0)  # +181 — нет
    assert rel_of(make_dataset, pos, [rec("A", "2025-11-06", 50)])["fill_ratio"] == pytest.approx(0.5)  # +180 — да


def test_reliability_overlapping_windows_no_double_count(make_dataset):
    pos = [po("A", supplier="S1", order="2025-01-10", promised="2025-04-10", ordered=100, po_id="P1"),
           po("A", supplier="S1", order="2025-02-10", promised="2025-05-10", ordered=100, po_id="P2")]
    # один приход 150 в обоих окнах: P1 получает 100, P2 — 50; сверх заказа не засчитывается
    r = rel_of(make_dataset, pos, [rec("A", "2025-04-01", 150)])
    assert r["qty_received"] == 150 and r["fill_ratio"] == pytest.approx(0.75)
    r2 = rel_of(make_dataset, pos, [rec("A", "2025-04-01", 500)])
    assert r2["qty_received"] == 200 and r2["fill_ratio"] == pytest.approx(1.0)


def test_reliability_real_data_summary(real_ds):
    rel = supplier_reliability(real_ds, Config(), AS_OF)
    assert rel["uplift"].between(1.0, 1.2).all()
    assert (rel["qty_received"] <= rel["qty_ordered"]).all()


def test_lot_rounding_from_config(make_dataset):
    rec_rows = [{"sku_id": "A", "receipt_date": "2025-01-01", "qty": 37}]
    ds = make_dataset(skus=[{"sku_id": "A"}], receipts=rec_rows)
    assert lot_size(ds)["A"] == 40
    assert lot_size(ds, Config())["A"] == 40
    assert lot_size(ds, Config(lot_rounding=((1000, 25), (float("inf"), 100))))["A"] == 25
