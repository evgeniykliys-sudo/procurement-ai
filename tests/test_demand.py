from datetime import date

import numpy as np
import pandas as pd
import pytest

from procurement.config import Config
from procurement.data import cut_dataset
from procurement.demand import build_demand, stock_on

AS_OF = date(2026, 8, 13)

# Доли года по месяцам (янв..дек), похожие на реальные категории.
HEATERS = [15, 5, 1, 1, 0.5, 0.5, 0.5, 0.5, 10, 36, 19, 11]
TRIMMERS = [2, 3, 7, 13, 28, 20, 15, 5, 2, 2, 1, 1]


def seasonal(start: str, n: int, shares, yearly: float, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    p0 = pd.Period(start, "M")
    tot = sum(shares)
    out = []
    for i in range(n):
        m = (p0 + i).month
        q = yearly * shares[m - 1] / tot * (1 + rng.uniform(-noise, noise))
        out.append(int(round(q)))
    return out


def per(res, sku):
    return res.per_sku.set_index("sku_id").loc[sku]


def full_stock(sku, start, end, skip=()):
    return [{"sku_id": sku, "month": str(m), "stock_qty": 50}
            for m in pd.period_range(start, end, freq="M") if str(m) not in skip]


# --- профили ---------------------------------------------------------------

def test_profiles_heaters_peak_and_faucets_flat(make_dataset):
    sales = []
    for i in range(4):
        sales += make_dataset.series(f"H{i}", "2021-01", seasonal("2021-01", 67, HEATERS, 1200, 0.2, i),
                                     category="space_heaters")
        sales += make_dataset.series(f"F{i}", "2021-01", seasonal("2021-01", 67, [1] * 12, 600, 0.25, 10 + i),
                                     category="sanitary_faucets")
    ds = make_dataset(sales=sales)
    prof = build_demand(ds, Config(), date(2026, 8, 1)).profiles.set_index(["category", "month"])["index"]
    assert prof["space_heaters"].mean() == pytest.approx(1.0)
    assert prof[("space_heaters", 10)] >= 3.0
    assert prof["sanitary_faucets"].between(0.7, 1.3).all()
    assert list(prof["sanitary_faucets"].index) == list(range(1, 13))


def test_profiles_on_real_data(real_ds):
    prof = build_demand(real_ds, Config(), AS_OF).profiles.set_index(["category", "month"])["index"]
    assert prof[("space_heaters", 10)] >= 3.0
    assert prof["sanitary_faucets"].between(0.7, 1.3).all()
    assert prof[("string_trimmers", 5)] == prof["string_trimmers"].max()


def test_profiles_on_real_data_cut_2025_08(real_ds):
    # на 2025-08-01 основная масса обогревателей заведена в 2024-09: у них нет полных окон 2×12,
    # профиль не должен держаться на нескольких старых мелких SKU (факт: октябрь — 5.2 / 4.2 к среднему)
    cut = date(2025, 8, 1)
    prof = build_demand(cut_dataset(real_ds, cut), Config(), cut).profiles
    prof = prof.set_index(["category", "month"])["index"]
    assert prof[("space_heaters", 10)] >= 3.0
    assert prof[("string_trimmers", 5)] == prof["string_trimmers"].max()


def test_forecast_follows_season(make_dataset):
    sales = make_dataset.series("T1", "2021-01", seasonal("2021-01", 67, TRIMMERS, 1000),
                                category="string_trimmers")
    res = build_demand(make_dataset(sales=sales), Config(), date(2026, 8, 1))
    fc = res.forecast.set_index("period")["qty"]
    assert fc[pd.Period("2027-05", "M")] > 5 * fc[pd.Period("2026-12", "M")]
    # год прогноза ≈ год продаж (ряд без тренда)
    year = fc[pd.Period("2026-08", "M"):pd.Period("2027-07", "M")].sum()
    assert year == pytest.approx(1000, rel=0.1)


# --- уровень, цензура, неполный месяц -------------------------------------

def test_censored_months_do_not_pull_level_down(make_dataset):
    # 2024-01..2026-07 ровно 10 шт./мес; у S1 2026-01..2026-06 остатка нет (строк нет) и продаж нет
    gap = [f"2026-0{m}" for m in range(1, 7)]
    sales = make_dataset.series("S1", "2024-01", [10] * 24 + [0] * 6 + [10], category="sanitary_faucets")
    sales += make_dataset.series("S2", "2024-01", [10] * 31, category="sanitary_faucets")
    stock = full_stock("S1", "2024-01", "2026-07", skip=gap) + full_stock("S2", "2024-01", "2026-07")
    res = build_demand(make_dataset(sales=sales, stock=stock), Config(), date(2026, 8, 1))
    s1 = per(res, "S1")
    assert s1["censored_months"] == 6
    assert s1["base_monthly"] == pytest.approx(10, rel=0.05)
    assert s1["trend"] == pytest.approx(1.0, abs=0.05)
    assert s1["sales_12m"] == 60  # продажи показываются как есть


def test_zero_sales_with_stock_is_real_zero_demand(make_dataset):
    sales = make_dataset.series("S1", "2024-01", [10] * 24 + [0] * 6 + [10], category="sanitary_faucets")
    stock = full_stock("S1", "2024-01", "2026-07")
    s1 = per(build_demand(make_dataset(sales=sales, stock=stock), Config(), date(2026, 8, 1)), "S1")
    assert s1["censored_months"] == 0
    assert s1["base_monthly"] == pytest.approx(5, rel=0.1)


def test_no_censoring_before_stock_snapshots_exist(make_dataset):
    # снимки только с 2024-01: нули 2023-07..2023-12 — честный спрос 0, хотя строк остатка нет
    sales = make_dataset.series("S1", "2023-01", [10] * 6 + [0] * 6 + [10] * 6, category="sanitary_faucets")
    stock = full_stock("S1", "2024-01", "2024-07")
    s1 = per(build_demand(make_dataset(sales=sales, stock=stock), Config(), date(2024, 7, 1)), "S1")
    assert s1["censored_months"] == 0
    assert s1["base_monthly"] == pytest.approx(5, rel=0.05)


def test_partial_month_is_ignored(make_dataset):
    flat = make_dataset.series("S1", "2023-01", [10] * 43 + [500], category="sanitary_faucets",
                               partial_last=True)
    s1 = per(build_demand(make_dataset(sales=flat), Config(), AS_OF), "S1")
    assert s1["base_monthly"] == pytest.approx(10)
    assert s1["sales_12m"] == 120
    assert s1["history_months"] == 43


# --- тренд, новинки, мёртвые ----------------------------------------------

def test_trend_is_ratio_of_last_12_to_previous_12_clipped(make_dataset):
    # крупный ровный SKU держит профиль категории плоским: тест — про тренд, не про сезон
    sales = make_dataset.series("BIG", "2021-01", [1000] * 67, category="sanitary_faucets")
    sales += make_dataset.series("G", "2024-08", [10] * 12 + [12] * 12, category="sanitary_faucets")
    sales += make_dataset.series("X", "2024-08", [10] * 12 + [30] * 12, category="sanitary_faucets")
    sales += make_dataset.series("D", "2024-08", [10] * 12 + [2] * 12, category="sanitary_faucets")
    cfg = Config(shrink_min_months=36)  # профиль — только категорийный (ровный)
    res = build_demand(make_dataset(sales=sales), cfg, date(2026, 8, 1))
    assert per(res, "G")["trend"] == pytest.approx(1.2, abs=0.02)
    assert per(res, "X")["trend"] == 1.5
    assert per(res, "D")["trend"] == 0.5
    # прогноз = уровень × тренд × сезон
    fc = res.forecast[res.forecast["sku_id"] == "G"].set_index("period")["qty"]
    assert fc[pd.Period("2026-09", "M")] == pytest.approx(12 * 1.2, rel=0.03)


def test_trend_clip_comes_from_config(make_dataset):
    sales = make_dataset.series("X", "2024-08", [10] * 12 + [30] * 12, category="sanitary_faucets")
    res = build_demand(make_dataset(sales=sales), Config(trend_clip=(0.8, 1.2)), date(2026, 8, 1))
    assert per(res, "X")["trend"] == 1.2


def test_new_sku_uses_available_months_and_flag(make_dataset):
    sales = make_dataset.series("OLD", "2021-01", seasonal("2021-01", 67, TRIMMERS, 1000),
                                category="string_trimmers")
    sales += make_dataset.series("NEW", "2026-05", [100, 80, 60], category="string_trimmers")
    res = build_demand(make_dataset(sales=sales), Config(), date(2026, 8, 1))
    new = per(res, "NEW")
    assert "мало истории" in new["flags"]
    assert new["history_months"] == 3
    assert new["trend"] == 1.0
    # профиль = доли TRIMMERS × 12 / 99: май 3.39, июнь 2.42, июль 1.82; продажи / коэффициент —
    # 29.5, 33.0, 33.0; уровень новинки — медиана = 80 / (20 × 12 / 99) = 33.0
    assert new["base_monthly"] == pytest.approx(33.0, rel=0.02)
    assert "мало истории" not in per(res, "OLD")["flags"]


def test_new_sku_level_ignores_launch_shipment(make_dataset):
    # ровная категория (профиль = 1); новинка: стартовая отгрузка 600, дальше 40 и 50 — уровень 50 (медиана)
    sales = make_dataset.series("OLD", "2021-01", [1000] * 67, category="sanitary_faucets")
    sales += make_dataset.series("NEW", "2026-05", [600, 40, 50], category="sanitary_faucets")
    new = per(build_demand(make_dataset(sales=sales), Config(), date(2026, 8, 1)), "NEW")
    assert new["base_monthly"] <= 100
    assert new["base_monthly"] == pytest.approx(50, rel=0.02)


def test_new_faucets_forecast_on_real_data_not_inflated_by_launch(real_ds):
    res = build_demand(real_ds, Config(), AS_OF)
    ps = res.per_sku.set_index("sku_id")
    new = ps[(ps["category"] == "sanitary_faucets") & ps["flags"].map(lambda f: "мало истории" in f)]
    assert len(new) > 0
    fc = res.forecast[(res.forecast["period"] >= pd.Period("2026-08", "M"))
                      & (res.forecast["period"] <= pd.Period("2027-07", "M"))]
    fc12 = fc.groupby("sku_id")["qty"].sum()
    assert fc12[new.index].sum() <= 2 * new["sales_12m"].sum()
    # SKU-762245: 622 / 43 / 50 шт. (май—июль 2026), темп ~45/мес
    assert fc12["SKU-762245"] <= 2 * ps.loc["SKU-762245", "sales_12m"]


# --- уровень по месяцам не сезона ------------------------------------------

def _trimmers_base_and_stock(make_dataset):
    # крупный SKU задаёт профиль категории TRIMMERS и всегда в наличии
    sales = make_dataset.series("OLD", "2021-01", seasonal("2021-01", 55, TRIMMERS, 100_000),
                                category="string_trimmers")
    return sales, full_stock("OLD", "2024-01", "2025-07")


def test_level_from_off_season_months_only_is_not_blown_up(make_dataset):
    # разовая продажа 1200 шт. в октябре (коэффициент 0.24), дальше 9 мес без остатка и без продаж
    sales, stock = _trimmers_base_and_stock(make_dataset)
    sales += make_dataset.series("ODD", "2024-10", [1200] + [0] * 9, category="string_trimmers")
    stock += [{"sku_id": "ODD", "month": "2024-10", "stock_qty": 1200}]
    cut = date(2025, 8, 1)
    res = build_demand(make_dataset(sales=sales, stock=stock), Config(), cut)
    odd = per(res, "ODD")
    fc = res.forecast[res.forecast["sku_id"] == "ODD"].set_index("period")["qty"]
    year = fc[pd.Period("2025-08", "M"):pd.Period("2026-07", "M")].sum()
    assert odd["sales_12m"] == 1200
    assert year <= 2 * 1200
    # видны только месяцы не сезона и в 24 мес тоже: средние продажи 10 мес истории = 120
    assert odd["base_monthly"] == pytest.approx(120)
    assert "уровень без сезонной поправки" in odd["flags"]


def test_level_falls_back_to_24_months_when_season_censored(make_dataset):
    # 2023-08..2024-07 — обычный год (1000 шт., доли TRIMMERS); 2024-08..2025-02 — вдвое больше;
    # 2025-03..2025-07 (сезон) — без остатка и без продаж
    sales, stock = _trimmers_base_and_stock(make_dataset)
    normal = seasonal("2023-08", 19, TRIMMERS, 1000)
    sales += make_dataset.series("GAP", "2023-08", normal[:12] + [2 * v for v in normal[12:]] + [0] * 5,
                                 category="string_trimmers")
    stock += full_stock("GAP", "2024-01", "2025-02")
    # потолок «год прогноза ≤ N× продаж 12 мес» снят: проверяется именно уровень по 24 мес
    cfg = Config(shrink_min_months=36, forecast_cap_vs_last_year=100.0)
    res = build_demand(make_dataset(sales=sales, stock=stock), cfg, date(2025, 8, 1))
    gap = per(res, "GAP")
    assert gap["censored_months"] == 5
    # окно 12 мес: только авг—фев (Σ долей 16 из 99, средний коэффициент 0.28) — мало;
    # 24 мес: обычный год 1000 шт. + 2 × 161 (авг 51, сен 20, окт 20, ноя 10, дек 10, янв 20, фев 30)
    # шт. / (12 + 16 × 12 / 99) коэффициентов
    assert gap["base_monthly"] == pytest.approx((1000 + 2 * 161) / (12 + 16 * 12 / 99), rel=0.03)
    assert "уровень по 24 мес" in gap["flags"]
    assert gap["trend"] == 1.0


def test_dead_sku_flag_and_zero_forecast(make_dataset):
    sales = make_dataset.series("Z", "2022-01", [5] * 40 + [0] * 15, category="sanitary_faucets")
    sales += make_dataset.series("A", "2022-01", [5] * 55, category="sanitary_faucets")
    res = build_demand(make_dataset(sales=sales), Config(), date(2026, 8, 1))
    assert "нет продаж 12 мес" in per(res, "Z")["flags"]
    assert "нет продаж 12 мес" not in per(res, "A")["flags"]
    assert res.forecast[res.forecast["sku_id"] == "Z"]["qty"].sum() == 0


def test_dead_flag_count_matches_erp_on_real_data(real_ds):
    res = build_demand(real_ds, Config(), AS_OF)
    dead = res.per_sku["flags"].map(lambda f: "нет продаж 12 мес" in f)
    assert len(res.per_sku) == 791
    # 378 SKU с пустой наценкой в справочнике, но у 47 из них продажи за 12 мес были
    # (себестоимость не проставлена) — без продаж ровно 331 (посчитано по sales_monthly.csv).
    assert dead.sum() == 331


def test_popularity_columns_and_rank(make_dataset):
    sales = make_dataset.series("A", "2025-08", [30] * 12, category="c1", orders=[3] * 12)
    sales += make_dataset.series("B", "2025-08", [10] * 12, category="c1", orders=[2] * 12)
    sales += make_dataset.series("C", "2025-08", [1] * 12, category="c2")
    res = build_demand(make_dataset(sales=sales), Config(), date(2026, 8, 1))
    a, b, c = per(res, "A"), per(res, "B"), per(res, "C")
    assert (a["sales_12m"], a["orders_12m"], a["rank_in_category"]) == (360, 36, 1)
    assert (b["sales_12m"], b["rank_in_category"]) == (120, 2)
    assert c["rank_in_category"] == 1


def test_forecast_shape_and_first_month_share(make_dataset):
    sales = make_dataset.series("S", "2023-01", [31] * 43 + [5], category="sanitary_faucets", partial_last=True)
    res = build_demand(make_dataset(sales=sales), Config(forecast_months=18), AS_OF)
    fc = res.forecast[res.forecast["sku_id"] == "S"].set_index("period")["qty"]
    assert len(fc) == 18
    assert fc.index[0] == pd.Period("2026-08", "M") and fc.index[-1] == pd.Period("2028-01", "M")
    assert fc.iloc[0] == pytest.approx(31 * 19 / 31)  # остаток августа с 13-го: 19 дн. из 31
    assert fc.iloc[1] == pytest.approx(31)


def test_sigma_reflects_noise(make_dataset):
    sales = make_dataset.series("FLAT", "2023-01", [20] * 43, category="sanitary_faucets")
    rng = np.random.default_rng(1)
    noisy = [int(v) for v in rng.integers(0, 41, size=43)]  # равномерно 0..40, среднее 20
    sales += make_dataset.series("NOISY", "2023-01", noisy, category="sanitary_faucets")
    res = build_demand(make_dataset(sales=sales), Config(), date(2026, 8, 1))
    assert per(res, "NOISY")["sigma_monthly"] > 10
    assert per(res, "FLAT")["sigma_monthly"] < per(res, "NOISY")["sigma_monthly"] / 2


# --- остаток на дату -------------------------------------------------------

def test_stock_on_snapshot_minus_sales_plus_receipts(make_dataset):
    sales = []
    for s in ("S1", "S2", "S3"):
        sales += make_dataset.series(s, "2026-07", [40, 30], partial_last=True)
    stock = [{"sku_id": "S1", "month": "2026-08", "stock_qty": 100},
             {"sku_id": "S2", "month": "2026-07", "stock_qty": 100},  # на 2026-08 строки нет = 0
             {"sku_id": "S3", "month": "2026-08", "stock_qty": 10}]
    receipts = [{"sku_id": "S1", "receipt_date": "2026-08-05", "qty": 50},
                {"sku_id": "S1", "receipt_date": "2026-07-20", "qty": 999},  # до 1-го — уже в снимке
                {"sku_id": "S2", "receipt_date": "2026-08-02", "qty": 40}]
    s = stock_on(make_dataset(sales=sales, stock=stock, receipts=receipts), AS_OF)
    assert s["S1"] == 100 - 30 + 50
    assert s["S2"] == 0 - 30 + 40
    assert s["S3"] == 0  # не ниже нуля


def test_stock_on_real_data_covers_all_skus(real_ds):
    s = stock_on(real_ds, AS_OF)
    assert len(s) == 791 and (s >= 0).all()



def _gap_dataset(make_dataset):
    sales, stock = _trimmers_base_and_stock(make_dataset)
    normal = seasonal("2023-08", 19, TRIMMERS, 1000)
    sales += make_dataset.series("GAP", "2023-08", normal[:12] + [2 * v for v in normal[12:]] + [0] * 5,
                                 category="string_trimmers")
    return make_dataset(sales=sales, stock=stock + full_stock("GAP", "2024-01", "2025-02"))


def test_forecast_capped_by_last_year_sales_keeps_season_shape(make_dataset):
    # GAP: продано за 12 мес 2 × 161 = 322 шт.; уровень по 24 мес ≈ 95/мес дал бы ≈ 1140 шт. на год
    ds, cut = _gap_dataset(make_dataset), date(2025, 8, 1)
    free = build_demand(ds, Config(shrink_min_months=36, forecast_cap_vs_last_year=100.0), cut)
    res = build_demand(ds, Config(shrink_min_months=36), cut)
    fc = res.forecast[res.forecast["sku_id"] == "GAP"].set_index("period")["qty"]
    fc_free = free.forecast[free.forecast["sku_id"] == "GAP"].set_index("period")["qty"]
    year = fc[pd.Period("2025-08", "M"):pd.Period("2026-07", "M")].sum()
    assert per(res, "GAP")["sales_12m"] == 322
    assert year == pytest.approx(2 * 322)
    # весь ряд (18 мес) уменьшен в одно и то же число раз — сезонная форма та же
    ratio = fc / fc_free
    assert ratio.max() == pytest.approx(ratio.min())
    assert "упущенный спрос — проверить" in per(res, "GAP")["flags"]
    assert "упущенный спрос — проверить" not in per(free, "GAP")["flags"]


def test_cap_does_not_touch_new_sku(make_dataset):
    # новинка 3 мес: 30 шт. в месяц, прогноз года ≈ 360 > 2 × 90 — не ограничивается
    sales = make_dataset.series("OLD", "2021-01", [1000] * 67, category="sanitary_faucets")
    sales += make_dataset.series("NEW", "2026-05", [30, 30, 30], category="sanitary_faucets")
    res = build_demand(make_dataset(sales=sales), Config(), date(2026, 8, 1))
    year = res.forecast[res.forecast["sku_id"] == "NEW"]["qty"].iloc[:12].sum()
    assert year == pytest.approx(360, rel=0.02)
    assert "упущенный спрос — проверить" not in per(res, "NEW")["flags"]


@pytest.mark.parametrize("as_of", [date(2025, 8, 1), AS_OF])
def test_year_forecast_not_above_twice_last_12m_sales_on_real_data(real_ds, as_of):
    # SKU-DFFDC0 (5 шт. за 12 мес), SKU-7A0241 (13), SKU-7838C1 (1029) на 2026-08-13;
    # SKU-EC20B9, SKU-197B08 на 2025-08-01 — у всех SKU с историей ≥ 12 мес
    ds = cut_dataset(real_ds, as_of) if as_of.day == 1 else real_ds
    res = build_demand(ds, Config(), as_of)
    ps = res.per_sku.set_index("sku_id")
    p0 = pd.Period(as_of, "M")
    fc = res.forecast[(res.forecast["period"] >= p0) & (res.forecast["period"] < p0 + 12)]
    fc12 = fc.groupby("sku_id")["qty"].sum().reindex(ps.index)
    old = ps["history_months"] >= 12
    over = fc12[old] - 2 * ps.loc[old, "sales_12m"]
    assert (over <= 1e-6).all(), over[over > 1e-6].sort_values().tail(5).to_dict()
