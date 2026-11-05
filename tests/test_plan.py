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
    assert e["safety"] == pytest.approx(Z["A"] * 10 * 5 ** 0.5, rel=0.005)  # окна по дням: месяцы разной длины
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


def test_calendar_repeat_below_min_order_moves_to_next_date(make_dataset):
    # тот же SKU, но минимальный заказ 220 шт.: первый заказ (≈ 234 шт. потребности) проходит → 300;
    # на первой дате повторного (≈ 21.03) потребность ≈ 200 < 220 → 0 шт.; заказ не пропадает, а
    # переносится на следующие даты, пока остаток к приходу не упадёт ещё на ≈ 20 шт. (≈ 6 дн.) —
    # всё ещё март, 220 → партия 100 → 300 шт.
    ds = build(make_dataset, sku("NOW", stock=0, lead=90))
    plan = plan_of(ds, Config(min_order_qty=220))
    r = row(plan, "NOW")
    assert r["status"] == "order_now" and r["order_qty"] == 300
    cal = plan.calendar.set_index("month")
    assert cal.loc["2024-01", "positions"] == 1 and cal.loc["2024-01", "qty"] == 300
    assert cal.loc["2024-03", "positions"] == 1 and cal.loc["2024-03", "qty"] == 300
    assert cal.loc["2024-02", "positions"] == 0


# --- ABC и уровень сервиса ------------------------------------------------------

def test_abc_by_sales_within_category_sets_service_level(make_dataset):
    ds = build(make_dataset, sku("A1", qty=800), sku("B1", qty=150), sku("C1", qty=50),
               sku("X", qty=10, category="cat_b"))
    plan = plan_of(ds)
    assert [row(plan, s)["abc"] for s in ("A1", "B1", "C1", "X")] == ["A", "B", "C", "A"]
    for s, q in (("A1", 800), ("B1", 150), ("C1", 50)):
        r = row(plan, s)
        sigma = q ** 0.5  # ровный ряд: σ = √уровня
        assert r["safety_stock"] == pytest.approx(Z[r["abc"]] * sigma * 5 ** 0.5, rel=0.01)  # окна по дням и округление до 0.1


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
    # ONE — 1 шт. в год: потребность на 150 дн. < 1 шт. → сегодня не заказываем (а не 0.4 → 10);
    # FIVE — 5 шт. в год: потребность ≈ 3 шт., шаг округления дал бы 10 → заказ не больше годового прогноза
    one = [0] * 11 + [1]
    five = [1, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 1]  # продажи в т.ч. в апреле–мае (горизонт)
    ds = build(make_dataset, sku("ONE", qtys=one * 3, stock=0), sku("FIVE", qtys=five * 3, stock=0))
    plan = plan_of(ds)
    o = row(plan, "ONE")
    # сегодня не заказываем; заказ «позже» 1 шт. к сезону — см. test_later_order_after_first_below_min_is_kept
    assert o["status"] != "order_now" and "ONE" not in set(plan.supplier_orders["sku_id"])
    assert o["order_qty"] <= max(1, o["forecast_12m"]) + 0.1
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


# --- сезонный страховой (D07) и предел округления до партии (D08) ----------------

def test_seasonal_safety_follows_season_of_coverage_window(make_dataset):
    # «обогреватель»: 1000 шт. в октябре, 20 — в остальные месяцы. Заказ сегодня (01.01) придёт
    # 31.03 и покроет апрель–май, где спрос ≈ 20/мес: страховой не больше 2 мес. спроса периода
    # покрытия (≈ 40), а не z·σ_года·√5 (σ по году с октябрьским пиком — сотни штук)
    peaks = iter([1000, 1000, 3000])  # последний пик втрое выше — σ за 12 мес большая
    q = [next(peaks) if m == 10 else 20 for m in list(range(1, 13)) * 3]
    ds = build(make_dataset, sku("HEAT", qtys=q, stock=0, lead=90, lot_qty=500))
    r = row(plan_of(ds), "HEAT")
    e = r["explain"]
    assert r["status"] == "order_now"
    assert e["safety"] <= 2 * e["forecast_horizon"] / 2 + 0.5  # потолок: 2 мес. спроса покрытия (R = 60 дн.)
    old_formula = Z["A"] * r["sigma_monthly"] * 5 ** 0.5   # прежний z·σ·√((L+R)/30)
    assert r["sigma_monthly"] > 50 and e["safety"] < 0.2 * old_formula
    # партия 500 при потребности ≈ 125 добавила бы ≈ 4 мес. спроса — округление шагом, а не до 500
    assert e["lot_source"] == "шаг" and r["order_qty"] < 500


def test_lot_rounding_limited_to_one_month_of_demand(make_dataset):
    # 100 шт./мес, типичная партия 500 (≤ половины годового): потребность ≈ 234 → до 500 — это +2.7 мес.
    # спроса сверх нужного → шаг 50 → 250; при пределе 3 мес. — партия 500
    ds = build(make_dataset, sku("L", qty=100, stock=0, lead=90, lot_qty=500))
    r = row(plan_of(ds), "L")
    assert r["explain"]["typical_lot"] == 500
    assert r["explain"]["lot_source"] == "шаг" and r["order_qty"] == 250
    r3 = row(plan_of(ds, Config(lot_max_overage_months=3)), "L")
    assert r3["explain"]["lot_source"] == "партия" and r3["order_qty"] == 500


def test_physical_projection_matches_day_by_day_lost_sales():
    """D12: «отражение в нуле» = подневная симуляция с потерянными продажами при нуле."""
    import numpy as np
    from procurement.plan import physical
    rng = np.random.default_rng(7)
    dem = rng.uniform(0, 5, 200)
    arr = np.zeros(201)
    arr[[0, 40, 120]] = [20, 150, 60]
    p_debt = 10 + np.cumsum(arr) - np.r_[0.0, np.cumsum(dem)]
    s, want = 10 + arr[0], []
    for t in range(201):
        want.append(s)
        if t < 200:
            s = max(s - dem[t], 0.0) + arr[t + 1]
    assert physical(p_debt, dem) == pytest.approx(want)


def test_lost_demand_before_transit_is_not_paid_from_it(make_dataset):
    """D12: товар кончится на 9-й день, партия в пути придёт 15.02, новый заказ — через 90 дн.
    Продажи в разрыве потеряны: партия их не «гасит», к приходу заказа на складе её остаток."""
    cfg = Config()
    # 400 шт. в пути закрывают спрос до марта — заказывать сейчас не нужно (было: «сейчас, 200 шт.»)
    ds = build(make_dataset, sku("SKU-X", qty=100, stock=30, transit=[("2024-02-15", 400)], lead=90))
    r = build_plan(ds, build_demand(ds, cfg, AS_OF), cfg, AS_OF).items.iloc[0]
    assert r["status"] == "order_later" and r["order_by"] > date(2024, 2, 15)
    # 250 шт. в пути: заказ сейчас, но к его приходу на складе ≈ 100 шт. от партии, а не 0 «в долг»
    ds = build(make_dataset, sku("SKU-Y", qty=100, stock=30, transit=[("2024-02-15", 250)], lead=90))
    r = build_plan(ds, build_demand(ds, cfg, AS_OF), cfg, AS_OF).items.iloc[0]
    e = r["explain"]
    assert r["status"] == "order_now"
    assert e["lost_to_arrival"] > 100 and e["stock_at_arrival"] > 90
    assert e["stock_at_arrival"] == pytest.approx(
        e["stock"] + e["transit_lead"] - e["forecast_lead"] + e["lost_to_arrival"], abs=0.2)
    assert e["gross"] == pytest.approx(e["forecast_horizon"] + e["safety"] - e["stock_at_arrival"] - e["transit"], abs=0.11)


def test_transit_after_stockout_does_not_cancel_preventable_order(make_dataset):
    """D14: 400 шт. при 100/мес кончатся 01.05, партия в пути придёт 30.05, заказ сегодня — 31.03.
    Поздняя партия не отменяет заказ, закрывающий разрыв: он не меньше спроса до её прихода."""
    ds = build(make_dataset, sku("LATE", stock=400, lead=90, transit=[("2024-05-30", 1500)]))
    plan = plan_of(ds)
    r = row(plan, "LATE")
    e = r["explain"]
    assert r["stockout_date"] == date(2024, 5, 1)
    assert r["status"] == "order_now" and r["order_qty"] > 0 and "LATE" in set(plan.supplier_orders["sku_id"])
    assert e["bridge"] > max(e["gross_window"], 0) and e["gross"] == e["bridge"]
    # к приходу ≈ 100 шт., спрос до 30.05 ≈ 197 шт. — разрыв ≈ 97 шт.
    assert 85 <= e["bridge"] <= 105 and r["order_qty"] >= e["bridge"]
    # партия приходит до того, как товар кончится, — заказывать сейчас не нужно (правило окна R прежнее)
    early = row(plan_of(build(make_dataset, sku("EARLY", stock=400, lead=90, transit=[("2024-04-20", 1500)]))), "EARLY")
    assert early["status"] != "order_now"


def test_order_needed_before_next_review_is_not_lost_with_short_cover(make_dataset):
    """Покрытие 7 дн. короче пересмотра 30 дн.: заказ на сегодня 0 шт., но нужный заказ — 18.01.
    К следующему пересмотру его уже пропустили бы: заказать сейчас, а не «заказ не нужен»."""
    ds = build(make_dataset, sku("SHORT", stock=380, lead=90))
    later = row(plan_of(ds, Config(default_cover_days=7, review_period_days=0)), "SHORT")
    assert later["status"] == "order_later" and later["order_qty"] > 0
    plan = plan_of(ds, Config(default_cover_days=7))
    r = row(plan, "SHORT")
    assert r["status"] == "order_now" and r["order_qty"] >= later["order_qty"]
    assert r["order_by"] == later["order_by"]
    assert r["explain"]["merged_until_review"] == r["order_qty"] and r["explain"]["final"] == r["order_qty"]
    assert plan.calendar.iloc[0]["qty"] == r["order_qty"]


def test_zero_safety_cap_still_orders_for_empty_stock(make_dataset):
    """safety_cap_months = 0 (страховой выключен): пустой склад при спросе — точка заказа."""
    r = row(plan_of(build(make_dataset, sku("ZERO", stock=0, lead=90)), Config(safety_cap_months=0)), "ZERO")
    assert "deficit_unavoidable" in r["risk"]
    assert r["status"] == "order_now" and r["order_qty"] > 0 and r["explain"]["safety"] == 0


def test_supplier_order_arrival_matches_card(make_dataset):
    """«Заказать сейчас» с датой «заказать до» в будущем: приход в выгрузке — как в карточке
    (заказ сегодня + плечо), на него и посчитано количество."""
    plan = plan_of(build(make_dataset, sku("R", stock=450, lead=90)), Config(review_period_days=45))
    r, o = row(plan, "R"), plan.supplier_orders.set_index("sku_id").loc["R"]
    assert r["status"] == "order_now" and r["order_by"] > AS_OF
    assert o["order_by"] == r["order_by"]
    assert o["expected_arrival"].isoformat() == r["explain"]["arrival"] == (AS_OF + timedelta(days=90)).isoformat()


def test_later_order_after_first_below_min_is_kept(make_dataset):
    """Первая потребность < 1 шт. (минимальный заказ), но на более позднюю дату расчёт даёт заказ —
    он остаётся в плане и календаре «заказать позже», как повторные заказы (а не «заказ не нужен»)."""
    one = [0] * 11 + [1]
    ds = build(make_dataset, sku("ONE", qtys=one * 3, stock=0))
    plan = plan_of(ds)
    r = row(plan, "ONE")
    assert r["status"] == "order_later" and r["order_qty"] >= 1 and r["explain"]["gross"] >= 1
    assert r["order_qty"] <= max(1, r["forecast_12m"]) + 0.1
    month = str(r["order_by"])[:7]
    cal = plan.calendar.set_index("month")
    if month in cal.index:
        assert cal.loc[month, "qty"] >= r["order_qty"]


def test_merged_order_not_above_cap(make_dataset):
    """Покрытие 7 дн., потолок — 1 мес. спроса: заказы до пересмотра объединяются, но вместе не
    больше потолка (было: потолок 100 шт., после объединения — 204+)."""
    ds = build(make_dataset, sku("CAP", stock=0, lead=90))
    plan = plan_of(ds, Config(default_cover_days=7, excess_cover_months=1))
    r = row(plan, "CAP")
    e = r["explain"]
    assert r["status"] == "order_now" and 0 < r["order_qty"] <= e["cap"] <= 110
    # остаток потребности до пересмотра сверх потолка — следующим заказом календаря в том же месяце
    assert e.get("merged_capped") and plan.calendar.iloc[0]["qty"] > r["order_qty"]


def test_zero_safety_catches_shortage_within_a_day(make_dataset):
    """Страховой 0: утром 30.04 ≈ 1.3 шт. при спросе ≈ 3.3 шт./дн., партия — утром 01.05. Нехватка
    внутри дня — точка заказа: заказ сегодня пришёл бы 31.03 и её закрыл."""
    ds = build(make_dataset, sku("DAY", stock=398, lead=90, transit=[("2024-05-01", 1000)]))
    r = row(plan_of(ds, Config(safety_cap_months=0)), "DAY")
    assert r["stockout_date"] is not None and r["stockout_date"] < date(2024, 5, 1)
    assert r["status"] in ("order_now", "order_later") and r["order_qty"] > 0
    assert r["order_by"] + timedelta(days=90) <= r["stockout_date"]  # было: заказ только на декабрь, после партии


def test_cover_starts_when_stock_runs_out_not_when_a_unit_is_lost(make_dataset):
    """D17: «хватит до» — первый день дефицита, а не день, когда потеряна целая штука: при нуле
    сегодня — сегодня, 0 дн. (было: медленная позиция с нулём «хватало» на месяц); 3 шт. при 1 шт./мес
    кончаются ≈ через 3 мес., а не через 4. Риск дефицита — по-прежнему по потере штуки."""
    ds = build(make_dataset, sku("ZERO", qty=1, stock=0, lot_qty=10), sku("SLOW3", qty=1, stock=3, lot_qty=10))
    plan = plan_of(ds)
    z, s = row(plan, "ZERO"), row(plan, "SLOW3")
    assert z["cover_days"] == 0 and z["stockout_date"] == AS_OF
    assert "deficit_unavoidable" in z["risk"]  # до прихода (90 дн.) теряется ≈ 3 шт.
    assert 85 <= s["cover_days"] <= 92 and s["stockout_date"] == AS_OF + timedelta(days=int(s["cover_days"]))
