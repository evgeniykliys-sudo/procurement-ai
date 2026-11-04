"""Сквозной прогон CLI на реальной выгрузке data/raw (во временные папки)."""
import json
import time

import pytest

from conftest import REAL_RAW
from procurement.__main__ import main


@pytest.fixture(scope="module")
def real_run(tmp_path_factory):
    base = tmp_path_factory.mktemp("run")
    common = ["--raw", str(REAL_RAW), "--history", str(base / "history")]
    assert main(["seed-history", *common]) == 0
    t0 = time.time()
    code = main(["run", "--no-ai", *common, "--out", str(base / "out"), "--site", str(base / "site")])
    elapsed = time.time() - t0
    assert code == 0
    load = lambda p: json.loads((base / p).read_text(encoding="utf-8"))
    return {"base": base, "elapsed": elapsed, "plan": load("out/plan.json"), "diff": load("out/diff.json"),
            "backtest": load("out/backtest.json")}


def test_run_writes_outputs_history_and_site(real_run):
    base = real_run["base"]
    assert real_run["elapsed"] < 60
    assert (base / "site" / "index.html").exists()
    assert sorted(p.name for p in (base / "history").iterdir()) == ["2026-07-01.json", "2026-08-13.json"]
    assert real_run["plan"]["as_of"] == "2026-08-13"
    assert real_run["diff"]["prev_as_of"] == "2026-07-01"
    assert len(real_run["backtest"]["by_category"]) == 6


def test_real_kpi_agree_with_data(real_run):
    plan = real_run["plan"]
    k, items = plan["kpi"], plan["items"]
    assert len(items) == 791
    assert k["dead"] == 331 and k["dead_sell"] + k["dead_close"] == 331
    assert k["review_low_markup"] == 68 and k["review_no_cost"] == 47 and k["review_price"] == 115
    no_markup = [r for r in items if r["markup_12m"] is None]
    assert len(no_markup) == 378
    assert all(r["status"] in ("review_price", "dead_sell", "dead_close") for r in no_markup)
    assert k["in_transit_future_qty"] == 17127
    # ни мёртвых, ни с наценкой ниже 25% / без себестоимости — в заказе
    banned = {r["sku_id"] for r in items if r["markup_12m"] is None or r["markup_12m"] < 25}
    assert not banned & {r["sku_id"] for r in plan["supplier_orders"]}
    assert not banned & {r["sku_id"] for r in items if r["status"] == "order_now"}
    assert k["order_now_positions"] > 0
    # «почему столько» сходится с количеством
    for r in items:
        if r["status"] in ("order_now", "order_later"):
            assert r["explain"]["final"] == r["order_qty"]


# SKU с товаром в пути и 1–7 днями нуля до прихода при потерях < 1 шт. (ревью таска 02, B1)
TRANSIT_GAP_SKUS = ["SKU-FE79E7", "SKU-032D7B", "SKU-A6E5E8", "SKU-CE59B5", "SKU-ED0D18",
                    "SKU-D78305", "SKU-8A2CD0"]


def test_short_gap_before_arrival_is_not_unavoidable_deficit(real_run):
    by_id = {r["sku_id"]: r for r in real_run["plan"]["items"]}
    for sid in TRANSIT_GAP_SKUS:
        r = by_id[sid]
        assert "deficit_unavoidable" not in r["risk"], sid
        assert r["cover_days"] is None or r["cover_days"] > 0, sid  # None — не кончится за горизонт проекции
    for r in real_run["plan"]["items"]:
        assert r["cover_days"] is None or type(r["cover_days"]) is int
        if "deficit_unavoidable" in r["risk"]:
            assert r["explain"]["lost_lead"] >= 1 - 0.05  # округление до 0.1


def test_calendar_supplier_labels_and_slow_orders(real_run):
    plan = real_run["plan"]
    k = plan["kpi"]
    # календарь с повторными заказами: за 6 мес позиций не меньше, чем «заказать сейчас»
    assert len(plan["calendar"]) == 6
    assert plan["calendar"][0]["positions"] == k["order_now_positions"]
    # D11: повторный заказ раньше следующего пересмотра входит в «заказать сейчас» — месяц даты
    # анализа в календаре совпадает с заказом сейчас и по штукам
    assert plan["calendar"][0]["qty"] == k["order_now_qty"]
    assert any((r["explain"] or {}).get("merged_until_review") for r in plan["items"])
    assert sum(m["positions"] for m in plan["calendar"]) >= k["order_now_positions"]
    # поставщик не заведён — метка вместо null
    so = plan["supplier_orders"]
    assert all(r["supplier_id"] is not None for r in so)
    assert sum(r["supplier_id"] == "—" for r in so) == k["order_now_no_supplier"]
    assert all(r["supplier_label"] == "поставщик не заведён" for r in so if r["supplier_id"] == "—")
    # медленные позиции: меньше 1 шт. не заказываем, заказ не больше годового прогноза
    for r in plan["items"]:
        if r["status"] == "order_now":
            assert r["explain"]["gross"] >= 1 and r["order_qty"] <= max(1, r["forecast_12m"]) + 0.1, r["sku_id"]


def test_history_is_compact_and_reproducible(real_run):
    hist = real_run["base"] / "history"
    before = {p.name: p.read_bytes() for p in hist.iterdir()}
    assert main(["seed-history", "--raw", str(REAL_RAW), "--history", str(hist)]) == 0
    assert (hist / "2026-07-01.json").read_bytes() == before["2026-07-01.json"]  # побайтно тот же
    for name, raw in before.items():
        data = json.loads(raw.decode("utf-8"))
        assert set(data) == {"as_of", "kpi", "items"} and len(data["items"]) >= 700
        assert set(data["items"][0]) == {"sku_id", "status", "order_qty", "risk", "category", "supplier_id"}
        assert len(raw) < 200_000, name
