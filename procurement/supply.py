"""Поставки: плечо, товар в пути (в том числе просроченный), надёжность поставщиков, кратность партии."""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from .config import Config
from .data import Dataset

CLOSED_STATUSES = ("closed", "completed")


def lead_times(ds: Dataset, cfg: Config) -> pd.DataFrame:
    """Плечо по SKU: медиана `planned_lead_days` его заказов (≤ 0 отброшены) → медиана поставщика
    (по его заказам, иначе из supplier_dim) → `cfg.lead_default_days`; затем в [пол; потолок].

    Колонки: sku_id, lead_days (int), lead_source ("sku" | "supplier" | "default").
    """
    po = ds.po[ds.po["planned_lead_days"] > 0]
    by_sku = po.groupby("sku_id")["planned_lead_days"].median()
    by_sup = po.dropna(subset=["supplier_id"]).groupby("supplier_id")["planned_lead_days"].median()
    dim = ds.suppliers.set_index("supplier_id")["median_planned_lead_days"]
    dim = dim[dim > 0]
    by_sup = by_sup.combine_first(dim)

    sku = ds.sku[["sku_id", "supplier_id"]]
    own = sku["sku_id"].map(by_sku)
    sup = sku["supplier_id"].map(by_sup)
    lead = own.fillna(sup).fillna(cfg.lead_default_days)
    source = pd.Series("default", index=sku.index, dtype=object)
    source[sup.notna()] = "supplier"
    source[own.notna()] = "sku"
    lead = lead.clip(cfg.lead_min_days, cfg.lead_max_days).map(lambda v: int(math.floor(v + 0.5)))
    return pd.DataFrame({"sku_id": sku["sku_id"].to_numpy(), "lead_days": lead.astype("int64").to_numpy(),
                         "lead_source": source.to_numpy()})


def in_transit(ds: Dataset, cfg: Config, as_of: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Открытые заказы с `qty_pending > 0`, размещённые не позже `as_of`.

    - плановая дата ≥ `as_of` → в пути, eta = плановая дата, late = False;
    - просрочка ≤ `cfg.overdue_grace_days` → в пути, eta = as_of + `cfg.overdue_eta_days`, late = True;
    - просрочка больше → не учитываются, идут в `stale` («уточнить у поставщика»).

    arrivals: sku_id, po_id, supplier_id, eta (date), qty, late (bool), promised_date
    stale: po_id, sku_id, supplier_id, promised_date, qty_pending, days_overdue
    """
    po = ds.po[(ds.po["status"] == "open") & (ds.po["qty_pending"] > 0) & (ds.po["order_date"] <= as_of)].copy()
    overdue = po["promised_date"].map(lambda d: (as_of - d).days)
    on_time = overdue <= 0
    grace = (overdue > 0) & (overdue <= cfg.overdue_grace_days)
    late_eta = as_of + timedelta(days=cfg.overdue_eta_days)

    arr = po[on_time | grace]
    arrivals = pd.DataFrame({
        "sku_id": arr["sku_id"].to_numpy(dtype=object),
        "po_id": arr["po_id"].to_numpy(dtype=object),
        "supplier_id": arr["supplier_id"].to_numpy(dtype=object),
        "eta": [late_eta if g else d for d, g in zip(arr["promised_date"], grace[arr.index])],
        "qty": arr["qty_pending"].astype("int64").to_numpy(),
        "late": grace[arr.index].astype(bool).to_numpy(),
        "promised_date": arr["promised_date"].to_numpy(dtype=object),
    })
    arrivals["eta"] = arrivals["eta"].astype(object)
    arrivals = arrivals.sort_values(["eta", "sku_id"], kind="stable").reset_index(drop=True)

    old = po[~(on_time | grace)]
    stale = pd.DataFrame({
        "po_id": old["po_id"].to_numpy(dtype=object),
        "sku_id": old["sku_id"].to_numpy(dtype=object),
        "supplier_id": old["supplier_id"].to_numpy(dtype=object),
        "promised_date": old["promised_date"].to_numpy(dtype=object),
        "qty_pending": old["qty_pending"].astype("int64").to_numpy(),
        "days_overdue": overdue[old.index].astype("int64").to_numpy(),
    }).sort_values("days_overdue", ascending=False, kind="stable").reset_index(drop=True)
    return arrivals, stale


def _received_by_receipts(po: pd.DataFrame, receipts: pd.DataFrame, window_days: int) -> pd.Series:
    """Получено по каждому заказу — из receipts.csv (поле `qty_received` закрытых заказов ERP
    почти всегда 0, D01): приходы того же SKU от того же поставщика в окне
    [order_date; promised_date + window_days], не больше `qty_ordered`. Заказы SKU
    разбирают приходы по очереди `order_date`; засчитанное одному заказу другому не достаётся."""
    got = pd.Series(0, index=po.index, dtype="int64")
    rc = receipts[(receipts["qty"] > 0) & receipts["supplier_id"].notna()]
    pools = {key: g.sort_values("receipt_date", kind="stable") for key, g in rc.groupby(["sku_id", "supplier_id"])}
    window = timedelta(days=window_days)
    ordered = po.sort_values("order_date", kind="stable")
    for key, orders in ordered.groupby(["sku_id", "supplier_id"], sort=False):
        pool = pools.get(key)
        if pool is None:
            continue
        dates = list(pool["receipt_date"])
        left = [int(v) for v in pool["qty"]]
        for idx, order_date, promised, need in zip(orders.index, orders["order_date"],
                                                   orders["promised_date"], orders["qty_ordered"]):
            need, end = int(need), promised + window
            for j, d in enumerate(dates):
                if need <= 0:
                    break
                if left[j] > 0 and order_date <= d <= end:
                    take = min(left[j], need)
                    left[j] -= take
                    need -= take
                    got[idx] += take
    return got


def supplier_reliability(ds: Dataset, cfg: Config, as_of: date) -> pd.DataFrame:
    """Доля исполнения = Σ получено / Σ заказано по закрытым/исполненным заказам старше
    `cfg.reliability_min_age_days` (по дате размещения); «получено» — по приходам receipts.csv
    (см. `_received_by_receipts`). Ниже `cfg.reliability_min_ratio` — поправка на недовоз
    uplift = 1 / доля, не больше `cfg.reliability_max_uplift`; иначе 1.

    Колонки: supplier_id, fill_ratio (NaN — нет истории или ни один приход не сопоставлен с заказами:
    «нет данных», uplift = 1), orders_n, uplift, qty_ordered, qty_received.
    """
    cutoff = as_of - timedelta(days=cfg.reliability_min_age_days)
    po = ds.po[ds.po["status"].isin(CLOSED_STATUSES) & (ds.po["order_date"] <= cutoff)
               & ds.po["supplier_id"].notna()].copy()
    po["got"] = _received_by_receipts(po, ds.receipts[ds.receipts["receipt_date"] < as_of],
                                      cfg.reliability_receipt_window_days)
    agg = po.groupby("supplier_id").agg(orders_n=("qty_ordered", "size"), qty_ordered=("qty_ordered", "sum"),
                                         qty_received=("got", "sum"))
    ids = pd.Index(pd.unique(pd.concat([ds.suppliers["supplier_id"], ds.po["supplier_id"].dropna(),
                                        ds.sku["supplier_id"].dropna()], ignore_index=True)), name="supplier_id")
    agg = agg.reindex(ids)
    agg["orders_n"] = agg["orders_n"].fillna(0).astype("int64")
    agg["qty_ordered"] = agg["qty_ordered"].fillna(0).astype("int64")
    agg["qty_received"] = agg["qty_received"].fillna(0).astype("int64")
    # ни одного сопоставленного прихода — данных нет (а не «исполнение 0%»): доля NaN, поправка 1
    ratio = (agg["qty_received"] / agg["qty_ordered"].where(agg["qty_ordered"] > 0)).where(agg["qty_received"] > 0)
    agg["fill_ratio"] = ratio

    def _uplift(r: float) -> float:
        if pd.isna(r) or r >= cfg.reliability_min_ratio:
            return 1.0
        return float(min(1.0 / r, cfg.reliability_max_uplift))

    agg["uplift"] = ratio.map(_uplift).astype("float64")
    return agg.reset_index()[["supplier_id", "fill_ratio", "orders_n", "uplift", "qty_ordered", "qty_received"]]


def _round_lot(median: float, steps) -> int:
    step = next((s for limit, s in steps if median < limit), steps[-1][1])
    return int(max(step, math.floor(median / step + 0.5) * step))


def lot_size(ds: Dataset, cfg: Config | None = None) -> pd.Series:
    """Кратность партии SKU: медиана его приходов, округлённая до шага из `cfg.lot_rounding`
    (по умолчанию: < 100 шт. — до 10, < 1000 — до 50, иначе — до 100); нет приходов — 1."""
    steps = (cfg or Config()).lot_rounding
    med = ds.receipts[ds.receipts["qty"] > 0].groupby("sku_id")["qty"].median()
    lots = ds.sku["sku_id"].map(med).map(lambda m: 1 if pd.isna(m) else _round_lot(float(m), steps))
    out = pd.Series(lots.astype("int64").to_numpy(), index=ds.sku["sku_id"].to_numpy(), name="lot")
    out.index.name = "sku_id"
    return out
