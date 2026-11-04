"""Бэктест: модель на данных до даты среза против факта и наивного «среднее 12 мес»."""
from datetime import date

import pytest

from procurement.backtest import run_backtest
from procurement.config import Config


def seasonal(month: int) -> int:
    return 1000 if month == 10 else 50


def test_backtest_seasonal_model_beats_naive(make_dataset):
    import pandas as pd
    start = pd.Period("2021-08", "M")
    qtys = [seasonal((start + k).month) for k in range(60)]  # 2021-08 … 2026-07
    sales = make_dataset.series("H", "2021-08", qtys, category="heat")
    sales += make_dataset.series("F", "2021-08", [100] * 60, category="flat")
    ds = make_dataset(sales=sales)
    bt = run_backtest(ds, Config(), cut=date(2025, 8, 1), months=12)
    cats = {c["category"]: c for c in bt["by_category"]}
    assert set(cats) == {"heat", "flat"}
    heat = cats["heat"]
    # наивный — плоские (1000 + 11·50)/12 ≈ 129 в месяц: ошибка ≈ 2·(1000−129)/1550 ≈ 1.12
    assert heat["wape_naive"] == pytest.approx(2 * (1000 - 1550 / 12) / 1550, abs=0.01)
    assert heat["wape_model"] < 0.1 < heat["wape_naive"]
    assert cats["flat"]["wape_model"] == pytest.approx(0, abs=0.01)
    assert abs(cats["flat"]["bias_model"]) < 0.01
    series = bt["series"]["heat"]
    assert [p["month"] for p in series][:2] == ["2025-08", "2025-09"] and len(series) == 12
    assert series[2]["actual"] == 1000  # 2025-10
    assert set(bt["total"]) >= {"wape_model", "wape_naive", "bias_model", "bias_naive"}


def test_backtest_real_data_seasonal_categories(real_ds):
    bt = run_backtest(real_ds, Config())
    cats = {c["category"]: c for c in bt["by_category"]}
    assert len(cats) == 6
    for cat in ("space_heaters", "string_trimmers"):
        assert cats[cat]["wape_model"] < cats[cat]["wape_naive"], cat
    assert all(len(v) == 12 for v in bt["series"].values())
