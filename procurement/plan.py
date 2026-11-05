"""Решение о закупке: сколько и когда заказать, на сколько хватит, риски дефицита и излишка.

Политика — order-up-to с ежемесячным пересмотром (спецификация, истории 11–22):
- горизонт = плечо L + покрытие R (`target_cover_days`, если > 0, иначе `default_cover_days`);
- страховой запас (D04, D07) = √(покрытие² + плечо²): покрытие = z(уровень сервиса ABC) ×
  σ_окна × √(R/30) по окну покрытия, плечо = z × σ_окна × √(L/30) по окну плеча; σ устойчивая —
  1.25 × среднее |продажи − уровень × сезонный коэффициент| по нецензурированным месяцам
  последних 12 (не ниже √уровня), в каждом окне масштабируется его сезоном (спрос окна / средний
  за год); страховой ≤ `safety_cap_months` месяцев спроса периода покрытия. У ровного спроса это
  прежнее z × σ × √((L+R)/30);
- потребность (D06: спрос до прихода при нулевом остатке потерян и не дозаказывается) =
  прогноз(L..L+R) + страховой − остаток к приходу − в пути с приходом в (L; L+R], где
  остаток к приходу — физический (D12): остаток + в пути до L − продажи до L, а продажи при нуле
  потеряны и тогда, когда после дефицита приходит партия в пути (она не уходит на потерянный спрос);
  без такой партии это max(0, остаток + в пути до L − прогноз(0..L));
  > 0 → × поправка на недовоз, вверх до кратности партии — медианы приходов, если она не больше
  половины годового прогноза и округление добавляет не больше `lot_max_overage_months` мес. спроса
  (D05, D08), иначе до шага `lot_rounding` по величине заказа;
- проекция остатка по дням (прогноз месяца — равномерно по его дням; приходы в пути — в дату
  прихода) даёт «хватит до» (день, когда потеряно ≥ `stockout_min_lost_qty` шт. спроса; дефицит
  неизбежен — если столько теряется до as_of + L) и «заказать до»: дата, когда остаток опустится ниже страхового
  (и приход уже размещённых заказов в пределах R его не поднимет или до этого прихода товар
  кончится, D14 — тогда заказ не меньше разрыва до прихода, `bridge`), − L;
- «заказать до» ≤ дата + `review_period_days` → заказать сейчас (до следующего пересмотра
  не дожить; заказ на сегодня 0 шт. — в него входит заказ следующей даты до пересмотра, D15),
  иначе — позже: количество оценивается тем же расчётом на дату заказа;
- мёртвые (нет продаж `dead_months` мес) и наценка ниже порога или не проставлена — не в заказ.

Одна физическая проекция остатка (`physical`, D12: продажи при нуле потеряны) — для «хватит до»,
«заказать до», количества, повторных заказов календаря, «дней без товара» и графика.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from statistics import NormalDist

import numpy as np
import pandas as pd

from .config import Config
from .data import Dataset
from .demand import DemandResult, flag_no_sales, stock_on
from .supply import in_transit, lead_times, lot_size, supplier_reliability

STATUSES = ("order_now", "order_later", "ok", "review_price", "dead_sell", "dead_close", "no_demand")
RISKS = ("deficit_unavoidable", "deficit_if_late", "excess", "late_po", "low_reliability")

FLAG_NO_COST = "нет себестоимости"
FLAG_LOW_MARKUP = "низкая наценка"
FLAG_RUNNING_OUT = "скоро кончится"
FLAG_LEAD_ESTIMATED = "плечо оценочное"
FLAG_LOT_OVER_YEAR = "партия больше годового спроса"

NO_SUPPLIER = "—"                        # поставщик в карточке не заведён (F2)
NO_SUPPLIER_LABEL = "поставщик не заведён"
LOT_MAX_YEAR_SHARE = 0.5   # партия берётся, только если не больше половины годового прогноза (D05)
SIGMA_MAD_FACTOR = 1.25    # σ ≈ 1.25 × среднее абсолютное отклонение (для нормального распределения)
CALENDAR_MONTHS = 6   # календарь заказов (спецификация, история 26)
EXTRA_MONTHS = 12     # проекция дальше прогноза: повтор его последних 12 мес (сезонная форма)
EPS = 1e-9

ITEM_COLUMNS = [
    "sku_id", "category", "supplier_id", "abc", "status", "order_qty", "order_by", "stockout_date",
    "cover_days", "stock_now", "in_transit_qty", "next_eta", "lead_days", "lead_source",
    "cover_target_days", "safety_stock", "demand_horizon", "reliability_uplift", "markup_12m",
    "risk", "flags", "explain",
]
EXTRA_COLUMNS = [
    "supply_type", "weight_kg", "sales_12m", "orders_12m", "returns_12m", "rank_in_category",
    "trend", "base_monthly", "sigma_monthly", "history_months", "forecast_12m",
]


@dataclass
class Plan:
    items: pd.DataFrame
    supplier_orders: pd.DataFrame
    calendar: pd.DataFrame
    stale_po: pd.DataFrame
    reliability: pd.DataFrame
    as_of: date
    kpi: dict
    arrivals: pd.DataFrame = field(default_factory=pd.DataFrame)
    series: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)


def abc_classes(sales_12m: pd.Series, category: pd.Series, thresholds) -> pd.Series:
    """ABC по продажам 12 мес внутри категории: позиция A, если доля продаж всех более
    продаваемых позиций категории < первого порога, B — < второго, иначе C; без продаж — C."""
    a_lim, b_lim = thresholds
    df = pd.DataFrame({"q": sales_12m.astype(float).to_numpy(), "c": category.to_numpy()})
    out = np.full(len(df), "C", dtype=object)
    for _, g in df.groupby("c", sort=False):
        total = g["q"].sum()
        if total <= 0:
            continue
        g = g.sort_values("q", ascending=False, kind="stable")
        before = ((g["q"].cumsum() - g["q"]) / total).to_numpy()
        cls = np.where(before < a_lim, "A", np.where(before < b_lim, "B", "C"))
        out[g.index.to_numpy()] = np.where(g["q"].to_numpy() > 0, cls, "C")
    return pd.Series(out, index=sales_12m.index)


def _step_for(qty: float, steps) -> int:
    return int(next((st for limit, st in steps if qty < limit), steps[-1][1]))


def robust_sigma(ds: Dataset, demand: DemandResult, ids: list, month0: pd.Period) -> np.ndarray:
    """σ для страхового (D04): 1.25 × среднее |продажи − уровень × коэффициент категории| по полным
    нецензурированным месяцам последних 12 (цензура — как в прогнозе: на 1-е остатка нет и продаж 0),
    не ниже √уровня (пуассоновский шум); нет месяцев — √уровня."""
    months = pd.period_range(month0 - 12, month0 - 1, freq="M")
    sales = ds.sales[(ds.sales["partial_month"] == 0) & ds.sales["month"].isin(months)]
    q = sales.pivot_table(index="sku_id", columns="month", values="qty_sold", aggfunc="sum")
    q = q.reindex(index=ids, columns=months).to_numpy(dtype=float)
    if not ds.stock.empty:
        st = ds.stock.pivot_table(index="sku_id", columns="month", values="stock_qty", aggfunc="sum")
        st = st.reindex(index=ids, columns=months).fillna(0).to_numpy(dtype=float)
        observed = np.array([m >= ds.stock["month"].min() for m in months])[None, :]
        q = np.where(observed & (st <= 0) & (q == 0), np.nan, q)
    per = demand.per_sku.set_index("sku_id").reindex(ids)
    base = per["base_monthly"].fillna(0).astype(float).to_numpy()
    prof = demand.profiles.set_index(["category", "month"])["index"]
    cats = per["category"].to_numpy()
    idx = np.array([[prof.get((c, m.month), 1.0) for m in months] for c in cats], dtype=float)
    idx = idx.reshape(len(ids), len(months))
    dev = np.abs(q - base[:, None] * idx)
    seen = ~np.isnan(dev)
    mad = np.where(seen, dev, 0.0).sum(axis=1) / np.maximum(seen.sum(axis=1), 1)
    return np.maximum(SIGMA_MAD_FACTOR * mad, np.sqrt(np.maximum(base, 0)))


def physical(p_debt: np.ndarray, dem: np.ndarray) -> np.ndarray:
    """Физический остаток на начало дня t (после приходов дня) по проекции «в долг» (D12).

    `p_debt[..., t]` = остаток + приходы дней ≤ t − спрос дней < t (длина D+1), `dem` — спрос по дням
    (длина D). Продажи при нуле потеряны: недостача конца дня копится как потерянный спрос и к
    проекции прибавляется (отражение в нуле) — партия, пришедшая после дефицита, на него не уходит."""
    lost_end = np.maximum.accumulate(np.maximum(dem - p_debt[..., :-1], 0.0), axis=-1)  # к концу дня t
    zero = np.zeros(lost_end.shape[:-1] + (1,))
    return p_debt + np.concatenate([zero, lost_end], axis=-1)


def _round_lot(qty: float, lot: int) -> int:
    if qty <= 0:
        return 0
    lot = max(int(lot), 1)
    return int(math.ceil(round(qty / lot, 9)) * lot)


def _r1(x) -> float:
    return float(round(float(x), 1))


def _days_until(as_of: date, months: int) -> int:
    return ((pd.Timestamp(as_of) + pd.DateOffset(months=months)).date() - as_of).days


def build_plan(ds: Dataset, demand: DemandResult, cfg: Config, as_of: date) -> Plan:
    sku = ds.sku.drop_duplicates("sku_id").reset_index(drop=True)
    ids = sku["sku_id"].tolist()
    n = len(ids)
    pos = {s: i for i, s in enumerate(ids)}
    month0 = pd.Period(as_of, "M")
    per = demand.per_sku.set_index("sku_id").reindex(ids)

    # --- входы по SKU -----------------------------------------------------------------
    stock = stock_on(ds, as_of).reindex(ids).fillna(0).astype("int64").to_numpy()
    lt = lead_times(ds, cfg).set_index("sku_id").reindex(ids)
    lead = lt["lead_days"].fillna(cfg.lead_default_days).astype(int).to_numpy()
    tcd = pd.to_numeric(sku["target_cover_days"], errors="coerce").fillna(0).to_numpy()
    cover_target = np.where(tcd > 0, np.rint(tcd), cfg.default_cover_days).astype(int)
    horizon = lead + cover_target
    lots = lot_size(ds, cfg).reindex(ids).fillna(1).astype(int).to_numpy()
    rel = supplier_reliability(ds, cfg, as_of)
    uplift = sku["supplier_id"].map(rel.set_index("supplier_id")["uplift"]).fillna(1.0).astype(float).to_numpy()
    arrivals, stale = in_transit(ds, cfg, as_of)
    markup = pd.to_numeric(sku["avg_markup_pct_12m"], errors="coerce").to_numpy()
    weights = pd.to_numeric(sku["weight_kg"], errors="coerce").to_numpy()

    abc = abc_classes(per["sales_12m"].fillna(0), per["category"].fillna(sku.set_index("sku_id")["category"]),
                      cfg.abc_thresholds).to_numpy()
    z = np.array([NormalDist().inv_cdf(cfg.service_level[c]) for c in abc])
    sigma = robust_sigma(ds, demand, ids, month0)

    # --- дневная шкала: прогноз (+ повтор последних 12 мес), приходы в пути ----------------
    n_fc = cfg.forecast_months
    fc = demand.forecast.pivot_table(index="sku_id", columns="period", values="qty", aggfunc="sum")
    fc_m = fc.reindex(index=ids, columns=pd.period_range(month0, periods=n_fc, freq="M")).fillna(0.0)
    fc_m = fc_m.to_numpy(dtype=float)
    tail = fc_m[:, -min(12, n_fc):]
    fc_all = np.hstack([fc_m] + [tail] * math.ceil(EXTRA_MONTHS / tail.shape[1]))[:, :n_fc + EXTRA_MONTHS]
    m_days = np.array([(month0 + k).days_in_month for k in range(fc_all.shape[1])])
    m_days[0] -= as_of.day - 1                                     # первый месяц — оставшиеся дни
    daily = np.repeat(fc_all / m_days, m_days, axis=1)             # спрос по дням [n, D]
    D = daily.shape[1]
    D_fc = int(m_days[:n_fc].sum())                                # дни самого прогноза
    arr_d = np.zeros((n, D + 1))
    for s, eta, q in zip(arrivals["sku_id"], arrivals["eta"], arrivals["qty"]):
        if s in pos:
            arr_d[pos[s], min(max((eta - as_of).days, 0), D)] += q
    cum_dem = np.hstack([np.zeros((n, 1)), np.cumsum(daily, axis=1)])  # спрос дней < t
    cum_arr = np.cumsum(arr_d, axis=1)                                   # приходы дней ≤ t
    p_start = stock[:, None] + cum_arr - cum_dem   # остаток на начало дня t после приходов, «в долг»
    phys_start = physical(p_start, daily)          # физический: продажи при нуле потеряны (D12)
    lost_cum = phys_start[:, 1:] - p_start[:, 1:]  # накопленный неудовлетворённый спрос к концу дня
    phys_end = np.maximum(p_start[:, :D] - daily + lost_cum, 0.0)  # физический остаток на конец дня
    lost_min = max(float(cfg.stockout_min_lost_qty), EPS)

    e_days = min(_days_until(as_of, cfg.excess_cover_months), D)
    y_days = min(_days_until(as_of, 12), D)
    fc_year = cum_dem[:, y_days]
    # страховой (D04, D07) для заказа с приходом в день t — две части, каждая по сезону своего окна:
    # покрытие (t; t+R] и плечо [t−L; t). σ — разброс месяца среднего уровня; в окне он
    # масштабируется отношением спроса окна к среднему за год (у обогревателя с приходом в феврале
    # разброс не октябрьский), не ниже пуассоновского √(уровень × отношение). Итог — √(покрытие² +
    # плечо²), не больше `safety_cap_months` месяцев спроса периода покрытия. У ровного спроса —
    # прежнее z·σ·√((L+R)/30). Страховой — постоянный уровень: и заказ после дефицита восстанавливает
    # его целиком, иначе следующий заказ остаётся без защиты от ошибки прогноза на плече.
    t_idx = np.arange(D + 1)[None, :]
    year_rate = fc_year / max(y_days, 1)
    base_lvl = np.maximum(per["base_monthly"].fillna(0).astype(float).to_numpy(), 0.0)

    def _window_part(lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        days = hi - lo
        dem = np.take_along_axis(cum_dem, hi, axis=1) - np.take_along_axis(cum_dem, lo, axis=1)
        rate = dem / np.maximum(days, 1)
        ratio = np.where(year_rate[:, None] > EPS, rate / np.where(year_rate > EPS, year_rate, 1.0)[:, None], 1.0)
        sig = np.maximum(sigma[:, None] * ratio, np.sqrt(base_lvl[:, None] * ratio))
        return z[:, None] * sig * np.sqrt(days / 30.0), rate * 30.0

    cov_hi = np.minimum(t_idx + cover_target[:, None], D)
    s_cover, cover_month = _window_part(np.broadcast_to(t_idx, cov_hi.shape).copy(), cov_hi)
    lead_lo = np.maximum(t_idx - lead[:, None], 0)
    s_lead, _ = _window_part(lead_lo, np.broadcast_to(t_idx, lead_lo.shape).copy())
    safety_cap = cfg.safety_cap_months * cover_month
    safety_t = np.minimum(np.sqrt(s_cover ** 2 + s_lead ** 2), safety_cap)   # [n, D+1]

    # партия (D05): медиана приходов, если она есть и не больше половины годового прогноза;
    # округление до неё — только если добавляет не больше `lot_max_overage_months` мес. спроса (D08)
    use_lot = (lots > 1) & (lots <= LOT_MAX_YEAR_SHARE * fc_year)
    lot_slack = cfg.lot_max_overage_months * fc_year / 12.0
    dead_flag = flag_no_sales(cfg)
    late_skus = set(arrivals.loc[arrivals["late"], "sku_id"]) | set(stale["sku_id"])
    transit_total = arrivals.groupby("sku_id")["qty"].sum()
    next_eta = arrivals.groupby("sku_id")["eta"].min()

    cal_days = (pd.Period(month0 + CALENDAR_MONTHS, "M").start_time.date() - as_of).days  # окно календаря

    def next_cross(i: int, proj: np.ndarray, start: int) -> int | None:
        """Первый день t ≥ start, когда проекция ниже страхового и либо не поднимается до t + R,
        либо до прихода, который её поднимет, теряется ≥ `stockout_min_lost_qty` шт. спроса (D14:
        партия в пути, пришедшая после дефицита, не отменяет заказ, который его предотвращает).
        Пустой склад при спросе дня — ниже страхового и при страховом 0 (`safety_cap_months = 0`)."""
        R = int(cover_target[i])
        below = proj < safety_t[i] - EPS
        below[:D] |= (proj[:D] <= EPS) & (daily[i] > EPS)
        for t in np.flatnonzero(below[start:D_fc]) + start:
            w = below[t:min(t + R, D) + 1]
            if w.all():
                return int(t)
            u = t + int(np.argmin(w))  # первый день выше страхового — его поднял приход
            if np.maximum(daily[i, t:u] - proj[t:u], 0.0).sum() >= lost_min - EPS:
                return int(t)
        return None

    def need_at(i: int, d: int, proj: np.ndarray, carr: np.ndarray | None = None) -> dict:
        """Потребность заказа, размещённого на день d (0 — сегодня): приход в день a = d + L.
        forecast_lead / transit_lead — спрос и приходы до a; stock_at_arrival — физический остаток к
        приходу (`proj` — физическая проекция, D12: продажи при нуле потеряны, в том числе до прихода
        партий в пути — партия не уходит на потерянный спрос); lost_to_arrival — сколько спроса до
        прихода потеряно; forecast_horizon / transit — покрытие (a; a+R]. `carr` — накопленные приходы
        SKU (по умолчанию — заказы в пути; для повторных заказов — с ними, `proj` — тоже с ними)."""
        L, R = int(lead[i]), int(cover_target[i])
        carr = cum_arr[i] if carr is None else carr
        a, end = min(d + L, D), min(d + L + R, D)
        f_lead = _r1(cum_dem[i, a] - cum_dem[i, d])
        t_lead = int(round(carr[a] - (carr[d] if d > 0 else 0.0)))
        st = int(stock[i]) if d == 0 else _r1(proj[d])
        at_arrival = max(0.0, _r1(proj[a]))
        lost_to_arrival = max(0.0, _r1(at_arrival - (st + t_lead - f_lead)))
        f_h = _r1(cum_dem[i, end] - cum_dem[i, a])
        tr = int(round(carr[end] - carr[a]))
        saf = _r1(safety_t[i, a])
        gross_window = round(f_h + saf - at_arrival - tr, 1)
        # разрыв до партии в пути (D14): если она приходит в окне покрытия уже после того, как товар
        # кончится, заказ не меньше спроса до неё — max по дням τ ∈ (a; a+R] спроса [a; τ) − остаток
        # к приходу − приходов (a; τ); без такой партии он меньше `gross_window` (нет страхового)
        bridge = 0.0
        if end > a:
            k = np.arange(a + 1, end + 1)
            short = (cum_dem[i, k] - cum_dem[i, a]) - (carr[k - 1] - carr[a]) - at_arrival
            bridge = max(0.0, _r1(short.max()))
        gross = bridge if bridge > max(gross_window, 0.0) else gross_window
        qty = gross * uplift[i]
        by_lot = bool(use_lot[i]) and _round_lot(qty, int(lots[i])) - qty <= lot_slack[i] + EPS
        lot = int(lots[i]) if by_lot else _step_for(qty, cfg.lot_rounding)
        lot_source = "партия" if by_lot else "шаг"
        # потолок (F3): не больше прогноза за excess_cover_months от даты заказа, но не меньше 1 шт.
        cap = int(math.floor(max(1.0, cum_dem[i, min(d + e_days, D)] - cum_dem[i, d]) + EPS))
        final = _round_lot(qty, lot) if gross >= cfg.min_order_qty and gross > 0 else 0
        if final > cap:
            final, lot_source = cap, "потолок"
        return {"forecast_horizon": f_h, "safety": saf, "safety_cover": _r1(s_cover[i, a]),
                "safety_lead": _r1(s_lead[i, a]), "safety_cap": _r1(safety_cap[i, a]),
                "typical_lot": int(lots[i]), "stock_at_arrival": at_arrival,
                "lost_to_arrival": lost_to_arrival, "transit": tr,
                "gross_window": gross_window, "bridge": bridge, "gross": gross, "uplift": float(round(uplift[i], 4)), "lot": lot, "final": final,
                "lot_source": lot_source, "cap": cap,
                "stock": st, "transit_lead": t_lead, "forecast_lead": f_lead,
                "at": (as_of + timedelta(days=d)).isoformat(), "arrival": (as_of + timedelta(days=a)).isoformat(),
                "horizon_days": L + R, "z": float(round(z[i], 4)), "sigma_monthly": _r1(sigma[i])}

    def orders_from(i: int, d: int, q: int, proj: np.ndarray) -> list[tuple[int, int]]:
        """Заказы SKU в окне календаря: первый (день d, q шт.) и повторные — [(день, шт.)]."""
        L = int(lead[i])
        debt, c2, p2 = p_start[i].copy(), cum_arr[i].copy(), proj
        out = []
        while d < cal_days:
            if q > 0:
                out.append((d, q))
                a = min(d + L, D)
                debt[a:] += q                    # приход заказа — в проекцию «в долг»,
                c2[a:] += q
                p2 = physical(debt, daily[i])    # физическая — заново: недостача до прихода потеряна
                start = a + 1
            else:
                start = d + L + 1
            t = next_cross(i, p2, start)
            if t is None:
                break
            d = t - L
            q = int(need_at(i, d, p2, c2)["final"])
        return out

    rows, series = [], {}
    schedule: list[tuple] = []   # (месяц, шт., sku_id, день) всех заказов на 6 мес — для календаря
    for i, sid in enumerate(ids):
        raw_flags = per["flags"].iloc[i]
        flags = list(raw_flags) if isinstance(raw_flags, list) else []
        L, R = int(lead[i]), int(cover_target[i])
        has_demand = cum_dem[i, D_fc] > EPS
        dem_pos = daily[i] > EPS

        # «хватит до»: первый день, к концу которого потеряно ≥ stockout_min_lost_qty шт. спроса
        # (физический остаток: продажи при нуле теряются, приходы в пути его пополняют)
        stockout_t = None
        # «заказать до»: первый день t ≥ L, когда остаток ниже страхового и приходы уже
        # размещённых заказов не поднимают его обратно до t + R; минус L
        cross_t = None
        # проекция для заказа — физическая (D12): продажи при нуле потеряны и до прихода заказа, и до
        # прихода партий в пути — партия в пути не уходит на погашение потерянного спроса
        proj = phys_start[i]
        if has_demand:
            hit = np.flatnonzero(lost_cum[i] >= lost_min - EPS)
            stockout_t = int(hit[0]) if hit.size else None
            cross_t = next_cross(i, proj, L)

        dead = dead_flag in flags
        mk = markup[i]
        low = not np.isnan(mk) and mk < cfg.min_markup_pct
        no_cost = np.isnan(mk) and not dead
        if no_cost:
            flags.append(FLAG_NO_COST)
        if low:
            flags.append(FLAG_LOW_MARKUP)
        supply_type = sku["supply_type"].iloc[i]
        if supply_type == "unknown" or lt["lead_source"].iloc[i] == "default":
            flags.append(FLAG_LEAD_ESTIMATED)

        # риски
        risk: list[str] = []
        days_short = 0
        if not dead and stockout_t is not None:
            if stockout_t < L:
                risk.append("deficit_unavoidable")
                days_short = int(((phys_end[i, :L] <= EPS) & dem_pos[:L]).sum())
            elif stockout_t < L + cfg.review_period_days:
                risk.append("deficit_if_late")
        tr_total = int(transit_total.get(sid, 0))
        cover_qty = float(stock[i] + tr_total)
        excess_qty = cover_qty - float(cum_dem[i, e_days])
        if not dead and cover_qty > 0 and excess_qty > EPS:
            risk.append("excess")
        if sid in late_skus:
            risk.append("late_po")

        # статус и количество
        explain = need_at(i, 0, proj)
        explain["excluded"] = None
        order_by = None
        if dead:
            status = "dead_sell" if cover_qty > 0 else "dead_close"
            explain["excluded"] = f"мёртвый ассортимент: {dead_flag}"
        elif low or no_cost:
            status = "review_price"
            explain["excluded"] = ("нет себестоимости: наценка не посчитана" if no_cost else
                                   f"наценка {mk:.0f}% ниже порога {cfg.min_markup_pct:g}%")
            if "deficit_unavoidable" in risk or "deficit_if_late" in risk:
                flags.append(FLAG_RUNNING_OUT)
        elif not has_demand:
            status = "no_demand"
        elif cross_t is None:
            status = "ok"
        else:
            # первый заказ — на дату пересечения (до пересмотра — сегодня); сегодня вышел 0 шт.
            # (покрытие R короче пересмотра: остаток к приходу ещё выше страхового) — следующая дата
            # пересечения, если она до пересмотра: к следующему пересмотру её уже пропустили бы
            ob_t = cross_t - L
            d0 = 0 if ob_t <= cfg.review_period_days else ob_t
            first = need_at(i, d0, proj)
            d1, q1 = d0, int(first["final"])
            while q1 <= 0 and d0 == 0:
                t = next_cross(i, proj, d1 + L + 1)
                if t is None or t - L > cfg.review_period_days:
                    break
                d1 = t - L
                q1 = int(need_at(i, d1, proj)["final"])
            if q1 <= 0:  # потребность меньше минимального заказа — не заказываем (F3)
                status, explain = "ok", first
                explain["excluded"] = (f"потребность {explain['gross']:g} шт. меньше минимального "
                                       f"заказа {cfg.min_order_qty:g} шт.")
            elif d1 <= cfg.review_period_days:
                # «заказать сейчас»: и тогда, когда заказ на сегодня 0 шт., а нужный заказ — раньше
                # следующего пересмотра (D11: до него не дожить); количество — объединение ниже
                status, explain = "order_now", need_at(i, 0, proj)
                order_by = as_of + timedelta(days=ob_t if first["final"] > 0 else d1)
                seq = orders_from(i, 0, int(explain["final"]), proj)
                if not any(d <= cfg.review_period_days for d, _ in seq):  # пересмотр длиннее календаря
                    seq = [(d1, q1)] + seq
            else:
                status, order_by = "order_later", as_of + timedelta(days=d1)
                explain = need_at(i, d1, proj)
                seq = orders_from(i, d1, q1, proj)
            explain["excluded"] = explain.get("excluded")
        if status in ("order_now", "order_later"):
            order_qty = int(explain["final"])
        else:
            explain["would_order"] = int(explain["final"])
            explain["final"] = 0
            order_qty = 0
        # «Заказать сейчас» покрывает всё до следующего пересмотра: заказы календаря с датой не позже
        # `review_period_days` (сезон набирает ход, а каждый заказ покрывает R дн.; или R короче
        # пересмотра) — в этот же заказ (D11)
        if status == "order_now":
            total = sum(q for d, q in seq if d <= cfg.review_period_days)
            if total > order_qty:
                explain["merged_until_review"] = int(total - order_qty)
                explain["final"] = order_qty = int(total)
        if order_qty > 0 and uplift[i] > 1.0 + EPS:
            risk.append("low_reliability")
        if order_qty > 0 and lots[i] > fc_year[i]:  # для сведения: типичный приход больше года спроса
            flags.append(FLAG_LOT_OVER_YEAR)
        explain["days_short"] = days_short
        explain["lost_lead"] = _r1(lost_cum[i, min(L, D) - 1]) if L > 0 else 0.0  # потеряно до as_of + L
        explain["excess_qty"] = _r1(excess_qty) if "excess" in risk else 0.0

        # календарь (B2): первый заказ, затем повторные — тот же расчёт на следующую дату, когда
        # остаток с учётом уже запланированных приходов опустится до страхового, пока дата в окне;
        # повторный на эту дату вышел 0 шт. (меньше минимального заказа) — следующая дата пересечения;
        # у «заказать сейчас» — от объединённого заказа сегодня
        if order_qty > 0:
            if status == "order_now":
                seq = orders_from(i, 0, order_qty, proj)
            for d, q in seq:
                schedule.append((str(pd.Period(as_of + timedelta(days=d), "M")), q, sid, d))

        p = per.iloc[i]
        num = lambda col, default=0: p[col] if pd.notna(p[col]) else default
        rows.append({
            "sku_id": sid, "category": sku["category"].iloc[i], "supplier_id": sku["supplier_id"].iloc[i],
            "abc": abc[i], "status": status, "order_qty": order_qty, "order_by": order_by,
            "stockout_date": as_of + timedelta(days=stockout_t) if stockout_t is not None else None,
            "cover_days": stockout_t, "stock_now": int(stock[i]), "in_transit_qty": tr_total,
            "next_eta": next_eta.get(sid), "lead_days": L, "lead_source": lt["lead_source"].iloc[i],
            "cover_target_days": R, "safety_stock": explain["safety"],
            "demand_horizon": _r1(cum_dem[i, min(int(horizon[i]), D)]),
            "reliability_uplift": float(round(uplift[i], 4)),
            "markup_12m": None if np.isnan(mk) else float(mk),
            "risk": [r for r in RISKS if r in risk], "flags": flags, "explain": explain,
            "supply_type": supply_type, "weight_kg": None if np.isnan(weights[i]) else float(weights[i]),
            "sales_12m": int(num("sales_12m")), "orders_12m": int(num("orders_12m")),
            "returns_12m": int(num("returns_12m")),
            "rank_in_category": int(p["rank_in_category"]) if pd.notna(p["rank_in_category"]) else None,
            "trend": float(round(num("trend", 1.0), 3)), "base_monthly": _r1(num("base_monthly")),
            "sigma_monthly": _r1(sigma[i]), "history_months": int(num("history_months")),
            "forecast_12m": _r1(cum_dem[i, y_days]),
        })
        series[sid] = _sku_series(ds, sid, month0, fc_m[i], phys_end[i], m_days[:n_fc])

    items = pd.DataFrame(rows, columns=ITEM_COLUMNS + EXTRA_COLUMNS)
    for col in ("cover_days", "rank_in_category"):  # целое или None, а не float с NaN (F4)
        items[col] = pd.Series([None if r[col] is None else int(r[col]) for r in rows], dtype=object)
    supplier_orders = _supplier_orders(items, as_of)
    return Plan(items=items, supplier_orders=supplier_orders, calendar=_calendar(schedule, month0),
                stale_po=stale, reliability=rel, as_of=as_of,
                kpi=_kpi(items, supplier_orders, arrivals, stale, rel),
                arrivals=arrivals, series=series, config=_config_dict(cfg))


def _sku_series(ds: Dataset, sid, month0, fc_row, phys_row, m_days) -> dict:
    """Ряды для карточки SKU: продажи за 36 мес, прогноз, проекция остатка на конец месяца."""
    s = ds.sales[(ds.sales["sku_id"] == sid) & (ds.sales["month"] >= month0 - 36)]
    ends = np.cumsum(m_days) - 1
    return {
        "history": [[str(m), int(q)] for m, q in zip(s["month"], s["qty_sold"])],
        "forecast": [[str(month0 + k), _r1(v)] for k, v in enumerate(fc_row)],
        "stock_proj": [[str(month0 + k), _r1(phys_row[e])] for k, e in enumerate(ends)],
    }


def _supplier_orders(items: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Позиции «заказать сейчас» по поставщикам: «разместить до» — «заказать до», но не раньше
    сегодняшней; ожидаемый приход — заказа, размещённого сегодня (дата анализа + плечо): на него
    посчитано количество, и ту же дату показывает карточка SKU; вес строки — вес единицы × кол-во
    (None — не заведён)."""
    now = items[(items["status"] == "order_now") & (items["order_qty"] > 0)]
    cols = ["supplier_id", "supplier_label", "sku_id", "order_qty", "weight_kg", "order_by",
            "expected_arrival"]
    rows = []
    for r in now.itertuples(index=False):
        place = max(r.order_by, as_of)
        w = r.weight_kg
        known = isinstance(r.supplier_id, str) and r.supplier_id.strip() != ""
        sup = r.supplier_id if known else NO_SUPPLIER
        rows.append([sup, sup if known else NO_SUPPLIER_LABEL, r.sku_id, int(r.order_qty),
                     None if w is None else float(round(w * r.order_qty, 2)),
                     place, as_of + timedelta(days=int(r.lead_days))])
    df = pd.DataFrame(rows, columns=cols)
    if not df.empty:
        df = df.sort_values(["supplier_id", "order_by", "sku_id"], na_position="last", kind="stable")
    return df.reset_index(drop=True)


def _calendar(schedule: list[tuple], month0: pd.Period) -> pd.DataFrame:
    """Позиций (разных SKU) и штук к заказу по месяцам (ближайшие 6) из расписания заказов
    `(месяц, шт., sku_id, день)`: «сейчас» — месяц даты анализа, «позже» и повторные заказы — месяц
    даты заказа с количеством на ту дату. Два заказа одного SKU в месяце (сезон набирает ход, а
    каждый заказ покрывает R дн.) — одна позиция, штуки складываются."""
    rows = []
    for k in range(CALENDAR_MONTHS):
        m = str(month0 + k)
        hits = [(q, sid) for mm, q, sid, _ in schedule if mm == m]
        rows.append({"month": m, "positions": len({sid for _, sid in hits}), "qty": int(sum(q for q, _ in hits))})
    return pd.DataFrame(rows, columns=["month", "positions", "qty"])


def _kpi(items, orders, arrivals, stale, rel) -> dict:
    st = items["status"]
    has = lambda r: int(items["risk"].map(lambda x: r in x).sum())
    flag = lambda f: items["flags"].map(lambda x: f in x)
    review = st == "review_price"
    return {
        "skus_total": int(len(items)),
        "order_now_positions": int(len(orders)),
        "order_now_qty": int(orders["order_qty"].sum()),
        "order_now_suppliers": int(orders.loc[orders["supplier_id"] != NO_SUPPLIER, "supplier_id"].nunique()),
        "order_now_no_supplier": int((orders["supplier_id"] == NO_SUPPLIER).sum()),
        "order_later_positions": int((st == "order_later").sum()),
        "ok": int((st == "ok").sum()),
        "no_demand": int((st == "no_demand").sum()),
        "deficit_unavoidable": has("deficit_unavoidable"),
        "deficit_unavoidable_out_now": int((items["risk"].map(lambda x: "deficit_unavoidable" in x)
                                            & (items["stock_now"] <= 0)).sum()),
        "deficit_if_late": has("deficit_if_late"),
        "excess": has("excess"),
        "late_po": has("late_po"),
        "low_reliability": has("low_reliability"),
        "review_price": int(review.sum()),
        "review_low_markup": int((review & flag(FLAG_LOW_MARKUP)).sum()),
        "review_no_cost": int((review & flag(FLAG_NO_COST)).sum()),
        "review_running_out": int((review & flag(FLAG_RUNNING_OUT)).sum()),
        "dead": int(st.isin(["dead_sell", "dead_close"]).sum()),
        "dead_sell": int((st == "dead_sell").sum()),
        "dead_close": int((st == "dead_close").sum()),
        "in_transit_lines": int(len(arrivals)),
        "in_transit_qty": int(arrivals["qty"].sum()),
        "in_transit_future_qty": int(arrivals.loc[~arrivals["late"].astype(bool), "qty"].sum()),
        "late_lines": int(arrivals["late"].astype(bool).sum()),
        "stale_po_lines": int(len(stale)),
        "stale_po_likely_received": int((stale["received_after_order"] >= stale["qty_pending"]).sum())
        if "received_after_order" in stale else 0,
        "low_reliability_suppliers": int((rel["uplift"] > 1.0).sum()),
    }


def _config_dict(cfg: Config) -> dict:
    out = _jsonable(asdict(cfg))
    out["lot_rounding"] = [["inf" if math.isinf(a) else a, b] for a, b in cfg.lot_rounding]
    return out


def _jsonable(v):
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, pd.Period):
        return str(v)
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) or math.isinf(v) else float(v)
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, np.ndarray)):
        return [_jsonable(x) for x in v]
    return v


def records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → список словарей, пригодных для JSON."""
    return [{k: _jsonable(v) for k, v in r.items()} for r in df.to_dict("records")]


def plan_to_json(plan: Plan) -> dict:
    """Сериализация плана: даты ISO, NaN → null. `series` — ряды для карточек SKU."""
    return {
        "as_of": plan.as_of.isoformat(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "kpi": _jsonable(plan.kpi),
        "items": records(plan.items),
        "supplier_orders": records(plan.supplier_orders),
        "calendar": records(plan.calendar),
        "arrivals": records(plan.arrivals),
        "stale_po": records(plan.stale_po),
        "reliability": records(plan.reliability),
        "config": plan.config,
        "series": _jsonable(plan.series),
    }
