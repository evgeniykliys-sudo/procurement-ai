"""Бэктест прогноза: модель, обученная на данных до даты среза, против факта и наивного прогноза.

- модель — `build_demand` на `cut_dataset(ds, cut)`, прогноз на `months` мес от месяца среза;
- наивный — «среднее последних 12 полных месяцев» до среза, ровно на каждый месяц;
- факт — продажи всех SKU категории за те же месяцы (в том числе новинок, которых на дату среза
  ещё не было: их не видит ни модель, ни наивный — так честнее для закупщика);
- точность — по категории и месяцу: WAPE = Σ|прогноз − факт| / Σ факт,
  смещение = Σ(прогноз − факт) / Σ факт (> 0 — перепрогноз).
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd

from .config import Config
from .data import Dataset, cut_dataset
from .demand import build_demand

NAIVE_MONTHS = 12


def _scores(df: pd.DataFrame) -> dict:
    act = float(df["actual"].sum())
    out = {"actual": round(act, 1)}
    for name in ("model", "naive"):
        err = df[name] - df["actual"]
        out[f"wape_{name}"] = round(float(err.abs().sum() / act), 4) if act > 0 else None
        out[f"bias_{name}"] = round(float(err.sum() / act), 4) if act > 0 else None
        out[name] = round(float(df[name].sum()), 1)
    return out


def run_backtest(ds: Dataset, cfg: Config, cut: date = date(2025, 8, 1), months: int = 12) -> dict:
    """`{cut, months, by_category:[{category, wape_model, wape_naive, bias_model, bias_naive, actual,
    model, naive}], total:{…}, series:{category:[{month, actual, model, naive}]}}`."""
    cds = cut_dataset(ds, cut)
    m0 = pd.Period(cut, "M")
    window = pd.period_range(m0, periods=months, freq="M")
    dem = build_demand(cds, replace(cfg, forecast_months=max(cfg.forecast_months, months)), cut)

    cat_of = cds.sku.drop_duplicates("sku_id").set_index("sku_id")["category"]
    fc = dem.forecast[dem.forecast["period"].isin(window)].copy()
    fc["category"] = fc["sku_id"].map(cat_of)
    model = fc.groupby(["category", "period"])["qty"].sum()

    hist = cds.sales[(cds.sales["month"] >= m0 - NAIVE_MONTHS) & (cds.sales["month"] < m0)]
    naive_month = hist.groupby("category")["qty_sold"].sum() / NAIVE_MONTHS

    fact = ds.sales[ds.sales["month"].isin(window) & (ds.sales["partial_month"] == 0)]
    actual = fact.groupby(["category", "month"])["qty_sold"].sum()

    rows = []
    for c in sorted(set(cat_of.unique()) | set(fact["category"].unique())):
        for p in window:
            rows.append({"category": c, "month": p, "actual": float(actual.get((c, p), 0.0)),
                         "model": float(model.get((c, p), 0.0)), "naive": float(naive_month.get(c, 0.0))})
    grid = pd.DataFrame(rows, columns=["category", "month", "actual", "model", "naive"])

    by_category, series = [], {}
    for c, g in grid.groupby("category", sort=True):
        by_category.append({"category": c, **_scores(g)})
        series[c] = [{"month": str(r.month), "actual": round(r.actual, 1), "model": round(r.model, 1),
                      "naive": round(r.naive, 1)} for r in g.itertuples(index=False)]
    return {"cut": cut.isoformat(), "months": months, "by_category": by_category,
            "total": _scores(grid), "series": series}
