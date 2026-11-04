"""Бэктест прогноза: модель, обученная на данных до даты среза, против факта и наивного прогноза.

- модель — `build_demand` на `cut_dataset(ds, cut)`, прогноз на `months` мес от месяца среза;
- наивный — «среднее последних 12 полных месяцев» до среза по каждому SKU, ровно на каждый месяц;
- факт — продажи SKU, которые продавались до среза, за те же месяцы; продажи новинок, заведённых
  после среза, не прогнозирует никто (ни модель, ни наивный) — они показываются отдельно
  (`new_sku_actual`) и в ошибку не входят: иначе смещение у категорий с обновлённым ассортиментом
  (расходники к триммерам — 137 из 177 тыс. шт.) говорило бы об ассортименте, а не о прогнозе;
- точность — по категории и месяцу: WAPE = Σ|прогноз − факт| / Σ факт,
  смещение = Σ(прогноз − факт) / Σ факт (> 0 — перепрогноз);
- точность по SKU (`wape_sku_*`) — та же WAPE, но по парам «SKU × месяц»: ошибки разных товаров
  категории не гасят друг друга, а закупка решается именно по SKU;
- продажи ограничены наличием (D10): прогноз продаж месяца модели и наивного = min(прогноз
  спроса, остаток на 1-е + приходы месяца) — месяц без товара не делает прогноз спроса «ошибкой»;
- срез по умолчанию — 1-е число месяца даты анализа минус `cfg.backtest_months`: при новых
  данных бэктест сдвигается вместе с ними.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd

from .config import Config
from .data import Dataset, cut_dataset, detect_as_of
from .demand import build_demand

NAIVE_MONTHS = 12


def default_cut(ds: Dataset, cfg: Config, as_of: date | None = None) -> date:
    """1-е число месяца даты анализа минус `cfg.backtest_months` мес. Дата анализа — `as_of` (прогон
    на прошлую дату: окно факта кончается до неё, данных из будущего бэктест не видит), иначе — по данным."""
    m = pd.Period(as_of or detect_as_of(ds, replace(cfg, as_of=None)), "M") - cfg.backtest_months
    return date(m.year, m.month, 1)


def _sku_wape(df: pd.DataFrame, name: str):
    act = float(df["actual"].sum())
    return round(float((df[name] - df["actual"]).abs().sum() / act), 4) if act > 0 else None


def _scores(df: pd.DataFrame) -> dict:
    act = float(df["actual"].sum())
    out = {"actual": round(act, 1)}
    for name in ("model", "naive"):
        err = df[name] - df["actual"]
        out[f"wape_{name}"] = round(float(err.abs().sum() / act), 4) if act > 0 else None
        out[f"bias_{name}"] = round(float(err.sum() / act), 4) if act > 0 else None
        out[name] = round(float(df[name].sum()), 1)
    return out


def run_backtest(ds: Dataset, cfg: Config, cut: date | None = None, months: int | None = None) -> dict:
    """`{cut, months, by_category:[{category, wape_model, wape_naive, bias_model, bias_naive,
    wape_sku_model, wape_sku_naive, actual, model, naive}], total:{…}, series:{category:[{month,
    actual, model, naive}]}}`."""
    months = months or cfg.backtest_months
    cut = cut or default_cut(ds, cfg)
    cds = cut_dataset(ds, cut)
    m0 = pd.Period(cut, "M")
    window = pd.period_range(m0, periods=months, freq="M")
    dem = build_demand(cds, replace(cfg, forecast_months=max(cfg.forecast_months, months)), cut)

    cat_of = cds.sku.drop_duplicates("sku_id").set_index("sku_id")["category"]
    fc = dem.forecast[dem.forecast["period"].isin(window)]
    hist = cds.sales[(cds.sales["month"] >= m0 - NAIVE_MONTHS) & (cds.sales["month"] < m0)]
    fact = ds.sales[ds.sales["month"].isin(window) & (ds.sales["partial_month"] == 0)]

    # сетка «SKU × месяц»: прогноз модели, наивный «среднее 12 мес до среза» этого SKU, факт
    fc_sku = fc.groupby(["sku_id", "period"])["qty"].sum()
    naive_sku = hist.groupby("sku_id")["qty_sold"].sum() / NAIVE_MONTHS
    act_sku = fact.groupby(["sku_id", "month"])["qty_sold"].sum()
    # оцениваются SKU, продававшиеся до среза: только их можно прогнозировать и заказывать по плану;
    # продажи новинок, заведённых после среза (D10), — отдельной строкой, в ошибку не входят
    known = set(cds.sales.loc[cds.sales["qty_sold"] > 0, "sku_id"])
    new_fact = fact[~fact["sku_id"].isin(known)]
    new_by_cat = new_fact.groupby("category")["qty_sold"].sum()
    fact = fact[fact["sku_id"].isin(known)]
    sku_ids = sorted(known)
    cat_all = pd.concat([cat_of, ds.sales.drop_duplicates("sku_id").set_index("sku_id")["category"]])
    cat_all = cat_all[~cat_all.index.duplicated()]
    idx = pd.MultiIndex.from_product([sku_ids, window], names=["sku_id", "month"])
    sg = pd.DataFrame(index=idx).reset_index()
    sg["category"] = sg["sku_id"].map(cat_all)
    sg["actual"] = act_sku.reindex(idx).fillna(0.0).to_numpy()
    sg["model"] = fc_sku.reindex(idx).fillna(0.0).to_numpy()
    sg["naive"] = sg["sku_id"].map(naive_sku).fillna(0.0).to_numpy()
    # доступность (D10): продать в месяце можно не больше, чем было — остаток на 1-е число (строки
    # нет = 0) + приходы месяца. Прогноз продаж модели и наивного = min(прогноз спроса, доступно):
    # месяц без товара не засчитывается в ошибку ни тому, ни другому (продаж не было из-за
    # отсутствия товара, а не спроса), а при товаре на складе прогноз сравнивается как есть.
    # До первого снимка остатков доступность неизвестна — прогноз как есть.
    sg["censored"] = False
    if not ds.stock.empty:
        st = ds.stock.groupby(["sku_id", "month"])["stock_qty"].sum().reindex(idx).fillna(0)
        rc = ds.receipts.assign(month=[pd.Period(d, "M") for d in ds.receipts["receipt_date"]])
        got = rc.groupby(["sku_id", "month"])["qty"].sum().reindex(idx).fillna(0)
        avail = (st.clip(lower=0) + got).to_numpy()
        observed = (sg["month"] >= ds.stock["month"].min()).to_numpy()
        for col in ("model", "naive"):
            sg[col] = np.where(observed, np.minimum(sg[col].to_numpy(), avail), sg[col].to_numpy())
        sg["censored"] = observed & (avail <= 0)
    ok = sg

    cats = sorted(set(cat_of.unique()) | set(fact["category"].unique()))
    gidx = pd.MultiIndex.from_product([cats, window], names=["category", "month"])
    grid = (ok.groupby(["category", "month"])[["actual", "model", "naive"]].sum()
            .reindex(gidx).fillna(0.0).reset_index())
    excluded = sg.groupby("category")["censored"].sum()

    by_category, series = [], {}
    for c, g in grid.groupby("category", sort=True):
        gs = ok[ok["category"] == c]
        by_category.append({"category": c, **_scores(g), "wape_sku_model": _sku_wape(gs, "model"),
                            "wape_sku_naive": _sku_wape(gs, "naive"),
                            "excluded_sku_months": int(excluded.get(c, 0)),
                            "new_sku_actual": int(new_by_cat.get(c, 0))})
        series[c] = [{"month": str(r.month), "actual": round(r.actual, 1), "model": round(r.model, 1),
                      "naive": round(r.naive, 1)} for r in g.itertuples(index=False)]
    total = {**_scores(grid), "wape_sku_model": _sku_wape(ok, "model"), "wape_sku_naive": _sku_wape(ok, "naive"),
             "excluded_sku_months": int(sg["censored"].sum()), "sku_months": int(len(sg)),
             "new_sku_actual": int(new_fact["qty_sold"].sum())}
    return {"cut": cut.isoformat(), "months": months, "by_category": by_category,
            "total": total, "series": series}


FLAG_WEAK_FORECAST = "прогноз категории не точнее наивного — проверить"


def weak_categories(backtest: dict) -> set[str]:
    """Категории, где по бэктесту ошибка модели по SKU не меньше, чем у наивного прогноза."""
    return {c["category"] for c in backtest.get("by_category", [])
            if c.get("wape_sku_model") is not None and c.get("wape_sku_naive") is not None
            and c["wape_sku_model"] >= c["wape_sku_naive"]}


def mark_weak_forecast(plan_json: dict, backtest: dict) -> dict:
    """Флаг позициям к заказу (сейчас и позже) из категорий, где модель по бэктесту не точнее
    наивного: закупщик видит, где количество стоит перепроверить; KPI `weak_forecast_orders`."""
    weak = weak_categories(backtest)
    n = 0
    for r in plan_json.get("items", []):
        if r.get("category") in weak and r.get("status") in ("order_now", "order_later"):
            if FLAG_WEAK_FORECAST not in r["flags"]:
                r["flags"].append(FLAG_WEAK_FORECAST)
            n += r.get("status") == "order_now"
    plan_json.setdefault("kpi", {})["weak_forecast_orders"] = n
    plan_json["kpi"]["weak_forecast_categories"] = sorted(weak)
    return plan_json
