"""Решение о закупке на синтетике: ручной пример «почему столько», статусы, риски, даты."""
from datetime import date, timedelta
from statistics import NormalDist

import pytest

from procurement.config import Config
from procurement.demand import build_demand
from procurement.plan import build_plan, plan_to_json

AS_OF = date(2024, 1, 1)  # продажи 2021-01…2023-12 полные → дата анализа 1-е число следующего месяца
Z = {lvl: NormalDist().inv_cdf(p) for lvl, p in {"A": 0.95, "B": 0.90, "C": 0.80}.items()}


def sku(sku_id, qty=100, stock=200, transit=(), lead=90, markup=40.0, supplier="SUP-1",
        category="cat_a", months=36, zero_last=0, received_ratio=1.0, supply_type="import",
        weight=None, lot_qty=100, qtys=None):
    """Описание одного SKU: продажи `qty`/мес (последние `zero_last` мес — 0) или ряд `qtys`
    (по месяцам до 2023-12), снимок остатка на 2024-01, заказы в пути [(плановая дата, шт.)],
    плечо по закрытому заказу, типичный приход `lot_qty`."""
    return dict(sku_id=sku_id, qty=qty, stock=stock, transit=list(transit), lead=lead, markup=markup,
                supplier=supplier, category=category, months=months if qtys is None else len(qtys),
                zero_last=zero_last, received_ratio=received_ratio, supply_type=supply_type,
                weight=weight, lot_qty=lot_qty, qtys=qtys)


def build(make_dataset, *specs):
    skus, sales, stock, pos, receipts = [], [], [], [], []
    for s in specs:
        sid, n = s["sku_id"], s["months"]
        qtys = s["qtys"] or [s["qty"]] * (n - s["zero_last"]) + [0] * s["zero_last"]
        start = str(pd_period("2023-12") - (n - 1))
        rows = make_dataset.series(sid, start, qtys, category=s["category"])
        for r in rows:
            r["markup_pct"] = s["markup"] if r["qty_sold"] > 0 else None
        sales += rows
        skus.append({"sku_id": sid, "category": s["category"], "supplier_id": s["supplier"],
                     "supply_type": s["supply_type"], "avg_markup_pct_12m": s["markup"],
                     "weight_kg": s["weight"]})
        if s["stock"]:
            stock.append({"sku_id": sid, "month": "2024-01", "stock_qty": s["stock"]})
        lot = s["lot_qty"]
        got = int(round(lot * s["received_ratio"]))
        pos.append({"po_id": f"PO-{sid}-C", "sku_id": sid, "supplier_id": s["supplier"],
                    "order_date": "2023-01-10", "promised_date": "2023-04-10",
                    "planned_lead_days": s["lead"], "status": "closed", "qty_ordered": lot,
                    "qty_received": got})
        if got:
            receipts.append({"sku_id": sid, "receipt_date": "2023-04-10", "qty": got,
                             "supplier_id": s["supplier"]})
        receipts.append({"sku_id": sid, "receipt_date": "2022-06-01", "qty": lot,
                         "supplier_id": s["supplier"]})
        for i, (promised, q) in enumerate(s["transit"]):
            pos.append({"po_id": f"PO-{sid}-T{i}", "sku_id": sid, "supplier_id": s["supplier"],
                        "order_date": "2023-11-01", "promised_date": promised,
                        "planned_lead_days": s["lead"], "status": "open", "qty_ordered": q,
                        "qty_pending": q})
    suppliers = [{"supplier_id": x} for x in dict.fromkeys(s["supplier"] for s in specs if s["supplier"])]
    return make_dataset(skus=skus, sales=sales, stock=stock, po=pos, receipts=receipts,
                        suppliers=suppliers)


def pd_period(m):
    import pandas as pd
    return pd.Period(m, freq="M")


def plan_of(ds, cfg=None):
    cfg = cfg or Config()
    return build_plan(ds, build_demand(ds, cfg, AS_OF), cfg, AS_OF)


def row(plan, sku_id):
    return plan.items.set_index("sku_id").loc[sku_id]


# --- ручной пример «почему столько» -----------------------------------------

def test_explain_adds_up_to_order_qty_manual_example(make_dataset):
    ds = build(make_dataset, sku("A", qty=100, stock=200,
                                 transit=[("2024-03-01", 100), ("2024-04-15", 50)]))
    plan = plan_of(ds)
    r = row(plan, "A")
    # L = 90 (плечо закрытого заказа), R = 60 (покрытие по умолчанию) → горизонт 150 дн. от 01.01.2024
    assert (r["lead_days"], r["cover_target_days"]) == (90, 60)
    e = r["explain"]
    # до прихода (дни 0…89): янв + фев = 200, 30 дн. марта = 100·30/31 → 296.8;
    # остаток к приходу = 200 + 100 (приход 01.03 ≤ L) − 296.8 = 3.2
    assert (e["stock"], e["transit_lead"]) == (200, 100)
    assert e["forecast_lead"] == pytest.approx(200 + 100 * 30 / 31, abs=0.06)
    assert e["stock_at_arrival"] == pytest.approx(300 - 200 - 100 * 30 / 31, abs=0.06)
    # покрытие (дни 90…149): 31 марта + апрель + 29 дн. мая = 100/31 + 100 + 100·29/31
    assert e["forecast_horizon"] == pytest.approx(100 / 31 + 100 + 100 * 29 / 31, abs=0.06)
    # σ = max(0 ошибок, √100) = 10; один SKU в категории → A; z(95%)·10·√(150/30)
    assert r["abc"] == "A"
    assert e["safety"] == pytest.approx(Z["A"] * 10 * 5 ** 0.5, abs=0.06)
    assert (e["transit"], e["uplift"], e["lot"]) == (50, 1.0, 100)  # приход 15.04 — в (L; L+R]
    assert e["gross"] == pytest.approx(e["forecast_horizon"] + e["safety"] - e["stock_at_arrival"] - 50, abs=1e-6)
    # 196.8 + 36.8 − 3.2 − 50 = 180.4 → вверх до партии 100 → 200
    assert e["final"] == r["order_qty"] == 200
    assert r["status"] == "order_now"
    so = plan.supplier_orders
    assert list(so["sku_id"]) == ["A"] and int(so["order_qty"].iloc[0]) == 200


def test_lost_demand_before_arrival_is_not_reordered(make_dataset):
    # 50 шт. при 100/мес кончатся ~16.01, заказ сегодня придёт через 90 дн.: спрос разрыва потерян
    ds = build(make_dataset, sku("GAP", stock=50, lead=90))
    r = row(plan_of(ds), "GAP")
    e = r["explain"]
    assert e["stock_at_arrival"] == 0
    assert e["forecast_horizon"] == pytest.approx(100 / 31 + 100 + 100 * 29 / 31, abs=0.06)
    # 196.8 + 36.8 − 0 − 0 = 233.6 → 300 (а не 493.5 + 36.8 − 50 = 480.3 → 500)
    assert e["gross"] == pytest.approx(e["forecast_horizon"] + e["safety"], abs=1e-6)
    assert e["final"] == r["order_qty"] == 300


def test_every_sku_has_a_row_with_interface_columns(make_dataset):
    ds = build(make_dataset, sku("A"), sku("B", zero_last=14, stock=0, markup=None))
    plan = plan_of(ds)
    cols = ["sku_id", "category", "supplier_id", "abc", "status", "order_qty", "order_by",
            "stockout_date", "cover_days", "stock_now", "in_transit_qty", "next_eta", "lead_days",
            "lead_source", "cover_target_days", "safety_stock", "demand_horizon",
            "reliability_uplift", "markup_12m", "risk", "flags", "explain"]
    assert set(cols) <= set(plan.items.columns)
    assert sorted(plan.items["sku_id"]) == ["A", "B"]
    js = plan_to_json(plan)
    assert js["as_of"] == "2024-01-01" and len(js["items"]) == 2
    import json
    json.dumps(js)  # сериализуется без ошибок (даты ISO)
    # «хватит на, дн.» — целое или null (у A есть дата обнуления, у мёртвого B спроса нет)
    cover = {r["sku_id"]: r["cover_days"] for r in js["items"]}
    assert type(cover["A"]) is int and cover["B"] is None


def test_order_without_supplier_is_labelled(make_dataset):
    ds = build(make_dataset, sku("NOSUP", stock=0, supplier=None), sku("SUP", stock=0, supplier="SUP-1"))
    plan = plan_of(ds)
    so = plan.supplier_orders.set_index("sku_id")
    assert so.loc["NOSUP", "supplier_id"] == "—"
    assert so.loc["NOSUP", "supplier_label"] == "поставщик не заведён"
    assert so.loc["SUP", "supplier_id"] == so.loc["SUP", "supplier_label"] == "SUP-1"
    assert plan.kpi["order_now_no_supplier"] == 1 and plan.kpi["order_now_suppliers"] == 1
    js = plan_to_json(plan)
    assert all(r["supplier_id"] is not None for r in js["supplier_orders"])


# --- в пути вычитается ---------------------------------------------------------

def test_transit_covering_need_means_no_order(make_dataset):
    # без заказа в пути — заказать сейчас; с 1000 шт. в пути (≈10 мес спроса) — сейчас не надо
    assert row(plan_of(build(make_dataset, sku("A", stock=200))), "A")["status"] == "order_now"
    ds = build(make_dataset, sku("A", stock=200, transit=[("2024-01-20", 1000)]))
    plan = plan_of(ds)
    r = row(plan, "A")
    assert r["status"] == "order_later" and r["order_by"] > AS_OF + timedelta(days=150)
    assert "A" not in set(plan.supplier_orders["sku_id"])
    assert r["in_transit_qty"] == 1000 and r["next_eta"] == date(2024, 1, 20)


# --- наценка и мёртвый ассортимент -------------------------------------------

def test_low_markup_goes_to_review_never_to_order(make_dataset):
    ds = build(make_dataset, sku("LOW", markup=20.0, stock=0))  # дефицит налицо
    plan = plan_of(ds)
    r = row(plan, "LOW")
    assert r["status"] == "review_price" and r["order_qty"] == 0
    assert "скоро кончится" in r["flags"]
    assert "LOW" not in set(plan.supplier_orders["sku_id"])


def test_markup_threshold_comes_from_config(make_dataset):
    ds = build(make_dataset, sku("MID", markup=30.0))
    assert row(plan_of(ds), "MID")["status"] == "order_now"
    assert row(plan_of(ds, Config(min_markup_pct=35)), "MID")["status"] == "review_price"


def test_missing_cost_with_sales_goes_to_review(make_dataset):
    ds = build(make_dataset, sku("NC", markup=None, stock=0))
    r = row(plan_of(ds), "NC")
    assert r["status"] == "review_price" and r["order_qty"] == 0
    assert "нет себестоимости" in r["flags"]


def test_dead_sku_sell_or_close(make_dataset):
    ds = build(make_dataset, sku("DS", zero_last=13, stock=50, markup=None),
               sku("DC", zero_last=13, stock=0, markup=None), sku("LIVE"))
    plan = plan_of(ds)
    assert row(plan, "DS")["status"] == "dead_sell"
    assert row(plan, "DC")["status"] == "dead_close"
    assert row(plan, "DS")["order_qty"] == 0 and row(plan, "DC")["order_qty"] == 0
    assert not {"DS", "DC"} & set(plan.supplier_orders["sku_id"])
    assert plan.kpi["dead"] == 2 and plan.kpi["dead_sell"] == 1 and plan.kpi["dead_close"] == 1


# --- риски ---------------------------------------------------------------------

def test_deficit_unavoidable_when_stock_runs_out_before_lead(make_dataset):
    ds = build(make_dataset, sku("EMPTY", stock=50, lead=90), sku("FULL", stock=600, lead=90))
    plan = plan_of(ds)
    e = row(plan, "EMPTY")
    # 50 шт. при 100/мес — ~15 дн., заказ сегодня придёт через 90
    assert "deficit_unavoidable" in e["risk"]
    assert e["stockout_date"] < AS_OF + timedelta(days=90)
    assert e["explain"]["days_short"] > 60
    assert "deficit_unavoidable" not in row(plan, "FULL")["risk"]


def test_zero_stock_with_arrival_in_three_days_is_not_unavoidable_deficit(make_dataset):
    # остатка нет, приход 30 шт. через 3 дн.; SLOW — 6 шт./мес: за 3 дн. теряется ≈ 0.58 шт. < 1,
    # FAST — 100 шт./мес: ≈ 9.7 шт. ≥ 1 — дефицит до прихода настоящий
    ds = build(make_dataset, sku("SLOW", qty=6, stock=0, lead=90, transit=[("2024-01-04", 30)]),
               sku("FAST", qty=100, stock=0, lead=90, transit=[("2024-01-04", 30)]))
    plan = plan_of(ds)
    s = row(plan, "SLOW")
    assert "deficit_unavoidable" not in s["risk"]
    # 0.58 шт. потеряно до прихода, 30 шт. хватит ещё на ≈ 5 мес: день, когда потеряна 1 шт., — июнь
    assert s["cover_days"] > 140 and s["stockout_date"] == AS_OF + timedelta(days=int(s["cover_days"]))
    f = row(plan, "FAST")
    assert "deficit_unavoidable" in f["risk"] and f["cover_days"] == 0  # 3.2 шт. потеряно в первый же день
    # порог — параметр: при 0.1 шт. и у SLOW дефицит до прихода засчитывается
    s01 = row(plan_of(ds, Config(stockout_min_lost_qty=0.1)), "SLOW")
    assert "deficit_unavoidable" in s01["risk"] and s01["cover_days"] < 3


def test_deficit_if_late_when_stockout_between_lead_and_next_review(make_dataset):
    # 330 шт. при 100/мес ≈ 100 дн.: заказ сегодня (90 дн.) успевает, через месяц (120 дн.) — нет
    ds = build(make_dataset, sku("TIGHT", stock=330, lead=90))
    r = row(plan_of(ds), "TIGHT")
    assert "deficit_if_late" in r["risk"] and "deficit_unavoidable" not in r["risk"]


def test_excess_when_cover_over_twelve_months(make_dataset):
    ds = build(make_dataset, sku("BIG", stock=1000, transit=[("2024-02-01", 500)]), sku("NORM", stock=300))
    plan = plan_of(ds)
    assert "excess" in row(plan, "BIG")["risk"]
    assert row(plan, "BIG")["explain"]["excess_qty"] == pytest.approx(1500 - 1200, abs=1)
    assert "excess" not in row(plan, "NORM")["risk"]
    assert "excess" not in row(plan_of(ds, Config(excess_cover_months=18)), "BIG")["risk"]


def test_late_po_and_low_reliability(make_dataset):
    ds = build(make_dataset,
               sku("LATE", transit=[("2023-12-01", 50)], supplier="SUP-OK"),  # просрочен на 31 дн.
               sku("UNREL", stock=0, received_ratio=0.5, supplier="SUP-BAD"))
    plan = plan_of(ds)
    assert "late_po" in row(plan, "LATE")["risk"]
    u = row(plan, "UNREL")
    assert "low_reliability" in u["risk"]
    assert u["reliability_uplift"] == pytest.approx(1.2)  # 1/0.5 = 2 → потолок ×1.2
    e = u["explain"]
    lot = e["lot"]
    assert e["final"] == u["order_qty"] == -(-round(e["gross"] * 1.2, 6) // lot) * lot


# --- даты: проекция, «заказать до», период пересмотра ---------------------------

def test_dates_follow_projection(make_dataset):
    # 1000 шт. при 100/мес — ровно 10 мес: кончатся 31.10.2024, первая потерянная штука — 01.11
    ds = build(make_dataset, sku("S", stock=1000, lead=90))
    r = row(plan_of(ds), "S")
    assert r["stockout_date"] == date(2024, 11, 1)
    assert r["cover_days"] == (r["stockout_date"] - AS_OF).days
    # до страхового запаса (≈36.8 шт. ≈ 11 дн.) — раньше обнуления; заказать за плечо до этого
    hit_safety = r["order_by"] + timedelta(days=90)
    assert timedelta(days=5) <= r["stockout_date"] - hit_safety <= timedelta(days=16)
    assert r["status"] == "order_later"
    assert r["order_qty"] > 0  # оценочное количество на дату заказа


def test_order_now_covers_until_next_review(make_dataset):
    # 450 шт. ≈ 137 дн.; до страхового ≈ 126 дн. → заказать до ≈ +36 дн.
    ds = build(make_dataset, sku("R", stock=450, lead=90))
    r30 = row(plan_of(ds), "R")
    r45 = row(plan_of(ds, Config(review_period_days=45)), "R")
    assert AS_OF + timedelta(days=30) < r30["order_by"] <= AS_OF + timedelta(days=45)
    assert r30["status"] == "order_later"
    assert r45["status"] == "order_now" and r45["order_qty"] > 0


def test_calendar_counts_now_and_later_by_month(make_dataset):
    ds = build(make_dataset, sku("NOW", stock=0), sku("LATER", stock=700))
    plan = plan_of(ds)
    cal = plan.calendar.set_index("month")
    assert len(plan.calendar) == 6
    jan, later_month = "2024-01", str(row(plan, "LATER")["order_by"])[:7]
    assert cal.loc[jan, "positions"] >= 1
    assert cal.loc[later_month, "positions"] >= 1
    # первые заказы обоих SKU в календаре; сверх них — повторные заказы (B2)
    assert cal["qty"].sum() >= row(plan, "NOW")["order_qty"] + row(plan, "LATER")["order_qty"]


def test_calendar_repeats_orders_within_six_months(make_dataset):
    # 100 шт./мес, остатка нет, L = 90, R = 60: заказ сегодня 300 шт. (233.6 → партия 100) придёт 31.03;
    # страховой ≈ 36.8 шт. ≈ 11 дн. → следующий заказ, когда остаток дойдёт до страхового:
    # (300 − 36.8) / 3.29 ≈ 80 дн. после прихода − L → ≈ 21.03; ему хватает 200 шт. (≈ 61 дн.) → ≈ 21.05;
    # следующий ≈ 21.07 — за пределами 6 мес (январь–июнь)
    ds = build(make_dataset, sku("NOW", stock=0, lead=90))
    plan = plan_of(ds)
    r = row(plan, "NOW")
    assert r["status"] == "order_now" and r["order_qty"] == 300
    cal = plan.calendar.set_index("month")
    assert list(cal["positions"]) == [1, 0, 1, 0, 1, 0]
    assert cal.loc["2024-01", "qty"] == 300
    assert cal.loc["2024-03", "qty"] == cal.loc["2024-05", "qty"] == 200
    assert cal["positions"].sum() >= plan.kpi["order_now_positions"]


# --- ABC и уровень сервиса ------------------------------------------------------

def test_abc_by_sales_within_category_sets_service_level(make_dataset):
    ds = build(make_dataset, sku("A1", qty=800), sku("B1", qty=150), sku("C1", qty=50),
               sku("X", qty=10, category="cat_b"))
    plan = plan_of(ds)
    assert [row(plan, s)["abc"] for s in ("A1", "B1", "C1", "X")] == ["A", "B", "C", "A"]
    for s, q in (("A1", 800), ("B1", 150), ("C1", 50)):
        r = row(plan, s)
        sigma = q ** 0.5  # ровный ряд: σ = √уровня
        assert r["safety_stock"] == pytest.approx(Z[r["abc"]] * sigma * 5 ** 0.5, abs=0.06)


def test_unknown_supply_type_marks_lead_as_estimated(make_dataset):
    ds = build(make_dataset, sku("U", supply_type="unknown"), sku("K"))
    plan = plan_of(ds)
    assert "плечо оценочное" in row(plan, "U")["flags"]
    assert "плечо оценочное" not in row(plan, "K")["flags"]


def test_lot_larger_than_year_demand_is_flagged(make_dataset):
    # 2 шт./мес, типичная партия — 100 шт. > половины годового спроса (24): партия не берётся,
    # округление шагом (< 100 шт. — до 10); флаг — для сведения
    ds = build(make_dataset, sku("SLOW", qty=2, stock=0), sku("FAST", qty=100, stock=0))
    plan = plan_of(ds)
    assert row(plan, "SLOW")["order_qty"] % 10 == 0 and 0 < row(plan, "SLOW")["order_qty"] < 100
    assert row(plan, "FAST")["explain"]["lot"] == 100
    assert "партия больше годового спроса" in row(plan, "SLOW")["flags"]
    assert "партия больше годового спроса" not in row(plan, "FAST")["flags"]


def test_lot_over_half_year_forecast_uses_rounding_step(make_dataset):
    # годовой прогноз ≈ 20 шт., типичный приход 1000: заказ кратен шагу (10), а не 1000
    q = [2, 1, 2, 2, 1, 2, 2, 1, 2, 2, 1, 2] * 3   # 20 шт. в год
    ds = build(make_dataset, sku("S20", qtys=q, stock=0, lot_qty=1000))
    r = row(plan_of(ds), "S20")
    assert r["forecast_12m"] == pytest.approx(20, abs=1.5)
    assert r["order_qty"] > 0 and r["order_qty"] % 10 == 0 and r["order_qty"] < 1000
    assert r["explain"]["lot"] == 10


def test_slow_sku_order_not_inflated_by_rounding_step(make_dataset):
    # ONE — 1 шт. в год: потребность на 150 дн. < 1 шт. → не заказываем (а не 0.4 → 10);
    # FIVE — 5 шт. в год: потребность ≈ 3 шт., шаг округления дал бы 10 → заказ не больше годового прогноза
    one = [0] * 11 + [1]
    five = [1, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 1]  # продажи в т.ч. в апреле–мае (горизонт)
    ds = build(make_dataset, sku("ONE", qtys=one * 3, stock=0), sku("FIVE", qtys=five * 3, stock=0))
    plan = plan_of(ds)
    o = row(plan, "ONE")
    assert o["explain"]["gross"] < 1 and o["order_qty"] == 0
    assert o["status"] not in ("order_now", "order_later") and "ONE" not in set(plan.supplier_orders["sku_id"])
    f = row(plan, "FIVE")
    assert f["status"] == "order_now" and f["forecast_12m"] == pytest.approx(5, abs=0.5)
    assert f["explain"]["gross"] <= f["order_qty"] <= max(1, f["forecast_12m"])
    assert f["explain"]["final"] == f["order_qty"]
    # минимальный заказ — параметр: при 5 шт. и FIVE не заказываем
    assert row(plan_of(ds, Config(min_order_qty=5)), "FIVE")["order_qty"] == 0


def test_safety_capped_for_intermittent_demand(make_dataset):
    # прерывистый спрос: 500 шт. раз в 5 мес (в среднем 100/мес) рядом с ровным SKU 1000/мес
    lumpy = [500 if k % 5 == 4 else 0 for k in range(36)]
    ds = build(make_dataset, sku("LUMPY", qtys=lumpy, stock=0), sku("FLAT", qty=1000, stock=5000))
    r = row(plan_of(ds), "LUMPY")
    month_fc = r["demand_horizon"] / ((r["lead_days"] + r["cover_target_days"]) / 30)
    assert r["safety_stock"] <= 2 * month_fc + 0.1
    assert r["safety_stock"] <= r["demand_horizon"]
    assert row(plan_of(ds, Config(safety_cap_months=1)), "LUMPY")["safety_stock"] <= month_fc + 0.1


def test_calendar_month_of_as_of_equals_order_now(make_dataset):
    # «заказать до» ≈ 05.02 (в следующем месяце), но в пределах пересмотра 45 дн. → заказать сейчас
    ds = build(make_dataset, sku("R", stock=450, lead=90), sku("NOW", stock=0))
    plan = plan_of(ds, Config(review_period_days=45))
    assert row(plan, "R")["status"] == "order_now" and row(plan, "R")["order_by"].month == 2
    cal = plan.calendar.set_index("month")
    assert cal.loc["2024-01", "positions"] == plan.kpi["order_now_positions"] == 2
    assert cal.loc["2024-02", "positions"] == 0
