"""Прогноз спроса: базовый уровень × тренд × сезонный коэффициент, с цензурой дефицита.

Модель (объяснима закупщику и проверяется бэктестом):
- берутся только полные месяцы до месяца даты анализа (`partial_month = 1` не участвует);
- месяц цензурирован (упущенный спрос), если на 1-е число остатка нет и продаж 0; до первого
  снимка остатков (в выгрузке — 2024-01) цензура не применяется: о дефиците там ничего не известно;
- сезонный профиль категории — «отношение к скользящему среднему» по суммарному ряду продаж
  категории (ассортимент меняется: у SKU, заведённых под сезон, своих полных окон 2×12 ещё нет):
  продажи месяца / центрированное среднее 2×12; по каждому календарному месяцу — Σ продаж /
  Σ средних за все годы, год, отклонившийся от медианы по годам больше чем в
  `season_outlier_factor` раз, обрезается до границы; затем среднее = 1;
- у SKU с историей ≥ `shrink_min_months` профиль — смесь своего (тот же расчёт по ряду SKU,
  без цензурированных месяцев) и категорийного (усадка);
- базовый уровень — десезонализированное среднее за 12 полных месяцев: Σ продаж / Σ коэффициентов
  по нецензурированным месяцам окна; у новинок (< `new_sku_months`) — медиана месяцев
  «продажи / коэффициент» (стартовая отгрузка не задаёт уровень);
- если средний коэффициент использованных месяцев ниже `level_min_season_index` (видны только
  месяцы не сезона — деление на малый коэффициент раздувает уровень), окно удлиняется до 24 мес,
  а если и там мало — уровень = средние продажи месяцев истории окна без сезонной поправки
  (у новинок — медиана); оба случая с флагом, тренд = 1;
- у SKU с историей ≥ 12 мес прогноз на ближайшие 12 мес не выше `forecast_cap_vs_last_year` ×
  продажи последних 12 мес: иначе уровень (и весь ряд) уменьшается пропорционально, флаг;
- тренд — уровень последних 12 мес / уровень предыдущих 12, в границах `trend_clip`
  (если и в предыдущих 12 мес сезона достаточно), в степени `trend_damping` (затухание);
- прогноз месяца = уровень × тренд × коэффициент месяца; первый месяц — доля оставшихся дней.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from .config import Config
from .data import Dataset

LEVEL_WINDOW = 12  # мес: окно базового уровня и каждой половины тренда (спецификация, R08)
MA_WEIGHTS = np.r_[0.5, np.ones(11), 0.5] / 12  # центрированное скользящее среднее 2×12

FLAG_NEW = "мало истории"
FLAG_ALL_CENSORED = "весь год без остатка"
FLAG_LEVEL_LONG = "уровень по 24 мес"
FLAG_LEVEL_PLAIN = "уровень без сезонной поправки"
FLAG_CAPPED = "упущенный спрос — проверить"


def flag_no_sales(cfg: Config) -> str:
    return f"нет продаж {cfg.dead_months} мес"


@dataclass
class DemandResult:
    profiles: pd.DataFrame  # category, month (1..12), index; среднее по категории = 1
    per_sku: pd.DataFrame   # см. PER_SKU_COLUMNS
    forecast: pd.DataFrame  # sku_id, period (Period M), qty


PER_SKU_COLUMNS = [
    "sku_id", "category", "base_monthly", "trend", "sigma_monthly", "history_months",
    "censored_months", "sales_12m", "orders_12m", "returns_12m", "rank_in_category", "flags",
]


def _month_period(d: date) -> pd.Period:
    return pd.Period(year=d.year, month=d.month, freq="M")


def stock_on(ds: Dataset, as_of: date) -> pd.Series:
    """Остаток на дату анализа: снимок на 1-е число − продажи с 1-го + приходы с 1-го (до `as_of`).

    Нет строки снимка = 0 (в выгрузке нет нулевых строк). Продажи неполного месяца вычитаются
    целиком (это и есть продажи до даты выгрузки), полного — пропорционально прошедшим дням.
    Результат не ниже 0; индекс — все SKU справочника.
    """
    month = _month_period(as_of)
    start = date(as_of.year, as_of.month, 1)
    ids = ds.sku["sku_id"]
    snap = ds.stock[ds.stock["month"] == month].groupby("sku_id")["stock_qty"].sum()
    sm = ds.sales[ds.sales["month"] == month]
    elapsed = (as_of.day - 1) / calendar.monthrange(as_of.year, as_of.month)[1]
    sold = (sm["qty_sold"] * np.where(sm["partial_month"] == 1, 1.0, elapsed)).groupby(sm["sku_id"]).sum()
    rc = ds.receipts
    rc = rc[(rc["receipt_date"] >= start) & (rc["receipt_date"] < as_of)]
    got = rc.groupby("sku_id")["qty"].sum()
    qty = ids.map(snap).fillna(0) - ids.map(sold).fillna(0) + ids.map(got).fillna(0)
    out = pd.Series(np.rint(qty.clip(lower=0).to_numpy()).astype("int64"), index=ids.to_numpy(),
                    name="stock_qty")
    out.index.name = "sku_id"
    return out


def _moving_average(q: np.ndarray) -> np.ndarray:
    """Центрированное 2×12 среднее по строкам; NaN, если в окне есть пропуск или край ряда."""
    n = q.shape[1]
    ma = np.full_like(q, np.nan, dtype=float)
    if n >= len(MA_WEIGHTS):
        win = np.lib.stride_tricks.sliding_window_view(q, len(MA_WEIGHTS), axis=1)
        ma[:, 6:n - 6] = (win * MA_WEIGHTS).sum(axis=-1)
    return ma


def _valid_pairs(q: np.ndarray, ma: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Продажи и скользящие средние там, где отношение определено (иначе 0 и 0)."""
    valid = ~np.isnan(q) & ~np.isnan(ma) & (np.nan_to_num(ma) > 0)
    return np.where(valid, q, 0.0), np.where(valid, ma, 0.0)


def _season_index(num: np.ndarray, den: np.ndarray, months: np.ndarray, outlier: float) -> np.ndarray:
    """Сезонный коэффициент каждого календарного месяца по годам: Σ продаж / Σ скользящих средних
    (вес года — его среднее, т. е. объём: почти пустые годы не перевешивают основные); NaN — нет данных.

    Отношение года, отличающееся от медианы по годам больше чем в `outlier` раз, обрезается до
    границы: разовая крупная отгрузка (у сантехники бывает ×2–3) не становится «сезоном», но и
    один тяжёлый год не определяет коэффициент целиком, как у взвешенной медианы."""
    ratio = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    out = np.full(12, np.nan)
    for m in range(12):
        sel = (months == m + 1) & ~np.isnan(ratio)
        if not sel.any():
            continue
        vals, w = ratio[sel], den[sel]
        med = float(np.median(vals))
        if med > 0:
            vals = np.clip(vals, med / outlier, med * outlier)
        out[m] = float((vals * w).sum() / w.sum())
    return out


def _normalize(idx: np.ndarray) -> np.ndarray:
    mean = idx.mean()
    return idx / mean if mean > 0 else np.ones(12)


def _level(q: np.ndarray, idx: np.ndarray, robust: bool = False) -> float:
    """Десезонализированный уровень: Σ продаж / Σ коэффициентов (NaN — месяц не учитывается);
    `robust` — медиана месяцев «продажи / коэффициент» (новинки: стартовая отгрузка не в счёт)."""
    ok = ~np.isnan(q) & (idx > 0)
    if not ok.any():
        return float("nan")
    if robust:
        return float(np.median(q[ok] / idx[ok]))
    return float(q[ok].sum() / idx[ok].sum())


def _enough_season(q: np.ndarray, idx: np.ndarray, min_index: float) -> bool:
    """Есть нецензурированные месяцы, и их средний сезонный коэффициент не ниже порога."""
    ok = ~np.isnan(q)
    return bool(ok.any()) and float(idx[ok].mean()) >= min_index


def _total_index(total: np.ndarray, months: np.ndarray, cfg: Config) -> np.ndarray | None:
    """Нормированный профиль суммарного ряда (None — ни одного полного окна 2×12)."""
    num, den = _valid_pairs(total[None, :], _moving_average(total[None, :]))
    raw = _season_index(num[0], den[0], months, cfg.season_outlier_factor)
    if np.isnan(raw).all():
        return None
    return _normalize(np.where(np.isnan(raw), np.nanmean(raw), raw))


def _category_index(q: np.ndarray, qc: np.ndarray, months: np.ndarray, cfg: Config) -> np.ndarray:
    """Профиль категории по сумме её SKU (до первой продажи категории — пусто).

    Цензурированный месяц в сумме был бы нулём и занижал бы сезон, когда товара нет: второй проход
    дооценивает его как уровень SKU (Σ продаж / Σ коэффициентов первого прохода по его
    нецензурированным месяцам, если сезона в них достаточно) × коэффициент месяца."""
    total = np.nansum(q, axis=0)
    total[np.isnan(q).all(axis=0)] = np.nan
    idx = _total_index(total, months, cfg)
    if idx is None:
        return np.ones(12)
    cens = np.isnan(qc) & ~np.isnan(q)
    if not cens.any():
        return idx
    it = idx[months - 1]
    seen = ~np.isnan(qc)
    n_seen = seen.sum(axis=1)
    idx_sum = (it[None, :] * seen).sum(axis=1)
    enough = (n_seen > 0) & (idx_sum >= cfg.level_min_season_index * np.maximum(n_seen, 1))
    lvl = np.where(enough, np.nansum(qc, axis=1) / np.where(idx_sum > 0, idx_sum, 1), 0.0)
    filled = total + (cens * lvl[:, None] * it[None, :]).sum(axis=0)
    return _total_index(filled, months, cfg)


def build_demand(ds: Dataset, cfg: Config, as_of: date) -> DemandResult:
    month0 = _month_period(as_of)
    sku = ds.sku[["sku_id", "category"]].drop_duplicates("sku_id").reset_index(drop=True)
    ids = sku["sku_id"].tolist()
    full = ds.sales[(ds.sales["partial_month"] == 0) & (ds.sales["month"] < month0)
                    & ds.sales["sku_id"].isin(ids)]

    first = full["month"].min() if not full.empty else month0 - 1
    periods = pd.period_range(min(first, month0 - 1), month0 - 1, freq="M")
    cols = pd.Index(periods)
    q = full.pivot_table(index="sku_id", columns="month", values="qty_sold", aggfunc="sum")
    q = q.reindex(index=ids, columns=cols).to_numpy(dtype=float)
    orders = full.pivot_table(index="sku_id", columns="month", values="orders_cnt", aggfunc="sum")
    orders = orders.reindex(index=ids, columns=cols).to_numpy(dtype=float)
    returns = full.pivot_table(index="sku_id", columns="month", values="qty_returned", aggfunc="sum")
    returns = returns.reindex(index=ids, columns=cols).to_numpy(dtype=float)
    months = np.array([p.month for p in periods])

    # цензура: остатка на 1-е нет (строки нет = 0) и продаж 0 — только там, где снимки уже есть
    censored = np.zeros_like(q, dtype=bool)
    if not ds.stock.empty:
        stock_start = ds.stock["month"].min()
        st = ds.stock.pivot_table(index="sku_id", columns="month", values="stock_qty", aggfunc="sum")
        st = st.reindex(index=ids, columns=cols).fillna(0).to_numpy(dtype=float)
        observed = np.array([p >= stock_start for p in periods])[None, :]
        censored = observed & (st <= 0) & (q == 0)
    qc = np.where(censored, np.nan, q)

    # среднее — по ряду продаж как есть (цензурированный месяц внутри окна = 0, иначе у товара,
    # которого летом нет на складе, не нашлось бы ни одного окна); в отношения цензурированные
    # месяцы не входят
    ma = _moving_average(q)
    num, den = _valid_pairs(qc, ma)

    # профили категорий — по суммарному ряду продаж категории
    cat_of = sku["category"].to_numpy()
    cat_idx = {cat: _category_index(q[cat_of == cat], qc[cat_of == cat], months, cfg)
               for cat in pd.unique(cat_of)}
    profiles = pd.DataFrame(
        [{"category": c, "month": m + 1, "index": float(v[m])} for c, v in cat_idx.items() for m in range(12)],
        columns=["category", "month", "index"],
    )

    lo, hi = cfg.trend_clip
    n_per = len(periods)
    win = slice(max(0, n_per - LEVEL_WINDOW), n_per)
    long = slice(max(0, n_per - 2 * LEVEL_WINDOW), n_per)
    prev = slice(max(0, n_per - 2 * LEVEL_WINDOW), max(0, n_per - LEVEL_WINDOW))
    dead_from = month0 - cfg.dead_months
    recent_sales = ds.sales[(ds.sales["month"] >= dead_from)
                            & ((ds.sales["month"] < month0)
                               | ((ds.sales["month"] == month0) & (ds.sales["partial_month"] == 1)))]
    sold_recently = set(recent_sales.loc[recent_sales["qty_sold"] > 0, "sku_id"])

    rows_out, fc_rows = [], []
    days_in = calendar.monthrange(as_of.year, as_of.month)[1]
    first_share = (days_in - as_of.day + 1) / days_in
    for i, sku_id in enumerate(ids):
        cidx = cat_idx[cat_of[i]]
        hist = ~np.isnan(q[i])
        history_months = int(hist.sum())
        flags: list[str] = []

        # сезонный профиль SKU: усадка своего к категорийному
        idx = cidx
        if history_months >= cfg.shrink_min_months:
            own = _season_index(num[i], den[i], months, cfg.season_outlier_factor)
            own = np.where(np.isnan(own), cidx, own)
            w = cfg.season_own_weight
            idx = _normalize(w * own + (1 - w) * cidx)
        idx_t = idx[months - 1]

        # базовый уровень
        qw, cw = qc[i, win], censored[i, win]
        censored_months = int(cw.sum())
        is_new = history_months < cfg.new_sku_months
        used = ~np.isnan(qw)
        if is_new:
            flags.append(FLAG_NEW)
        min_idx = cfg.level_min_season_index
        level_from_window = True
        if not used.any():
            base = float("nan")
            if censored_months > 0:
                # весь год без остатка и без продаж: спрос не наблюдался — уровень 0, флаг для закупщика
                flags.append(FLAG_ALL_CENSORED)
        elif _enough_season(qw, idx_t[win], min_idx):
            base = _level(qw, idx_t[win], robust=is_new)
        elif _enough_season(qc[i, long], idx_t[long], min_idx):
            base, level_from_window = _level(qc[i, long], idx_t[long], robust=is_new), False
            flags.append(FLAG_LEVEL_LONG)
        else:
            # видны только месяцы не сезона: продажи как есть, без деления на малый коэффициент
            seen = q[i, win][hist[win]]
            base = float(np.median(seen) if is_new else seen.mean())
            level_from_window = False
            flags.append(FLAG_LEVEL_PLAIN)
        base = 0.0 if np.isnan(base) else base

        # тренд — только если уровень из обычного окна и в предыдущих 12 мес сезона достаточно
        trend = 1.0
        if (level_from_window and not is_new and hist[prev].sum() == LEVEL_WINDOW
                and n_per >= 2 * LEVEL_WINDOW and _enough_season(qc[i, prev], idx_t[prev], min_idx)):
            l_prev = _level(qc[i, prev], idx_t[prev])
            if base > 0 and not np.isnan(l_prev) and l_prev > 0:
                trend = float(np.clip(base / l_prev, lo, hi)) ** cfg.trend_damping

        # потолок: год прогноза ≤ N× продаж последних 12 мес (кроме новинок) — уровень уменьшается,
        # весь ряд прогноза масштабируется пропорционально, сезонная форма та же
        sales_12m = int(np.nansum(q[i, win]))
        if history_months >= LEVEL_WINDOW:
            year_share = sum(idx[(month0 + k).month - 1] * (first_share if k == 0 else 1.0)
                             for k in range(LEVEL_WINDOW))
            year = base * trend * year_share
            cap = cfg.forecast_cap_vs_last_year * sales_12m
            if year > cap:
                if sales_12m > 0:
                    flags.append(FLAG_CAPPED)
                base = base * cap / year

        # σ месячных ошибок на окне уровня (не ниже пуассоновского шума √уровня)
        resid = (qw - base * idx_t[win])[used]
        rmse = float(np.sqrt((resid ** 2).sum() / (resid.size - 1))) if resid.size >= 2 else 0.0
        sigma = max(rmse, float(np.sqrt(base)))

        if sku_id not in sold_recently:
            flags.append(flag_no_sales(cfg))

        rows_out.append({
            "sku_id": sku_id, "category": cat_of[i], "base_monthly": base, "trend": trend,
            "sigma_monthly": sigma, "history_months": history_months, "censored_months": censored_months,
            "sales_12m": sales_12m, "orders_12m": int(np.nansum(orders[i, win])),
            "returns_12m": int(np.nansum(returns[i, win])), "flags": flags,
        })
        level = base * trend
        for k in range(cfg.forecast_months):
            p = month0 + k
            share = first_share if k == 0 else 1.0
            fc_rows.append((sku_id, p, level * idx[p.month - 1] * share))

    per_sku = pd.DataFrame(rows_out)
    if per_sku.empty:
        per_sku = pd.DataFrame(columns=PER_SKU_COLUMNS)
    else:
        per_sku["rank_in_category"] = (per_sku.groupby("category")["sales_12m"]
                                       .rank(method="min", ascending=False).astype("int64"))
        per_sku = per_sku[PER_SKU_COLUMNS]
    forecast = pd.DataFrame(fc_rows, columns=["sku_id", "period", "qty"])
    forecast["period"] = forecast["period"].astype("period[M]")
    return DemandResult(profiles=profiles, per_sku=per_sku, forecast=forecast)
