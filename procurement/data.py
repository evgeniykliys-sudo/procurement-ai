"""Чтение и проверка CSV-выгрузки ERP, дата анализа, обрезка набора на дату.

Внутри: месяцы — `pd.Period('M')`, даты — `datetime.date` (колонки object).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config


class DataError(Exception):
    """Ошибка входных данных; сообщение — по-русски, с именем файла и колонки."""


@dataclass
class Dataset:
    sku: pd.DataFrame
    sales: pd.DataFrame
    stock: pd.DataFrame
    po: pd.DataFrame
    receipts: pd.DataFrame
    suppliers: pd.DataFrame


# Схема: поле Dataset → (файл, {колонка: (тип, обязательно заполнена)}).
# Типы: str — строка, int/float — число, month — ГГГГ-ММ, date — ГГГГ-ММ-ДД.
SCHEMA: dict[str, tuple[str, dict[str, tuple[str, bool]]]] = {
    "sku": ("sku_dim.csv", {
        "sku_id": ("str", True), "category": ("str", True), "brand_id": ("str", False),
        "supplier_id": ("str", False), "supply_type": ("str", True), "uom": ("str", False),
        "weight_kg": ("float", False), "warranty_months": ("float", False),
        "target_cover_days": ("float", False), "price_decile": ("float", False),
        "first_sale_month": ("month", False), "last_sale_month": ("month", False),
        "months_with_sales": ("int", True), "qty_total": ("int", True),
        "avg_markup_pct_12m": ("float", False), "on_order_qty": ("int", True),
        "created": ("month", False),
    }),
    "sales": ("sales_monthly.csv", {
        "sku_id": ("str", True), "category": ("str", True), "month": ("month", True),
        "qty_sold": ("int", True), "qty_returned": ("int", True), "orders_cnt": ("int", True),
        "markup_pct": ("float", False), "partial_month": ("int", True),
    }),
    "stock": ("stock_monthly.csv", {
        "sku_id": ("str", True), "month": ("month", True), "stock_qty": ("int", True),
    }),
    "po": ("purchase_orders.csv", {
        "po_id": ("str", False), "sku_id": ("str", True), "supplier_id": ("str", False),
        "supply_type": ("str", False), "order_date": ("date", True),
        "promised_date": ("date", True), "planned_lead_days": ("int", True),
        "status": ("str", True), "qty_ordered": ("int", True), "qty_confirmed": ("int", True),
        "qty_received": ("int", True), "qty_pending": ("int", True),
    }),
    "receipts": ("receipts.csv", {
        "sku_id": ("str", True), "receipt_date": ("date", True), "qty": ("int", True),
        "supplier_id": ("str", False), "supply_type": ("str", False),
    }),
    "suppliers": ("supplier_dim.csv", {
        "supplier_id": ("str", True), "supply_type": ("str", False),
        "receipts_cnt": ("int", False), "skus": ("int", False),
        "median_days_between_receipts": ("float", False),
        "median_planned_lead_days": ("float", False),
    }),
}

_FORMATS = {"month": ("%Y-%m", "ГГГГ-ММ", "месяц"), "date": ("%Y-%m-%d", "ГГГГ-ММ-ДД", "дата")}


def _bad(fname: str, col: str, raw: pd.Series, mask: pd.Series, what: str) -> DataError:
    i = int(np.flatnonzero(mask.to_numpy())[0])
    value = raw.iloc[i]
    shown = "пусто" if pd.isna(value) else f"«{value}»"
    return DataError(f"Файл {fname}, колонка «{col}», строка {i + 2}: {what} ({shown}).")


def _parse_column(fname: str, col: str, raw: pd.Series, kind: str, required: bool) -> pd.Series:
    empty = (raw.isna() | (raw.astype("string").str.strip() == "")).fillna(True).astype(bool)
    if required and empty.any():
        raise _bad(fname, col, raw, empty, "пустое значение в обязательной колонке")
    if kind == "str":
        return pd.Series([None if e else str(v).strip() for v, e in zip(raw, empty)],
                         index=raw.index, dtype=object)
    if kind in ("int", "float"):
        num = pd.to_numeric(raw.where(~empty), errors="coerce")
        broken = num.isna() & ~empty
        if broken.any():
            raise _bad(fname, col, raw, broken, "не число")
        if kind == "int" and not empty.any():
            return num.round().astype("int64")
        return num.astype("float64")
    fmt, human, noun = _FORMATS[kind]
    parsed = pd.to_datetime(raw.where(~empty), format=fmt, errors="coerce")
    broken = parsed.isna() & ~empty
    if broken.any():
        raise _bad(fname, col, raw, broken, f"неверная {noun}, нужен формат {human}"
                   if kind == "date" else f"неверный {noun}, нужен формат {human}")
    if kind == "month":
        return parsed.dt.to_period("M")
    return pd.Series([None if pd.isna(d) else d.date() for d in parsed], index=raw.index, dtype=object)


def _read_table(raw_dir: Path, field: str) -> pd.DataFrame:
    fname, cols = SCHEMA[field]
    path = raw_dir / fname
    if not path.exists():
        raise DataError(f"Нет файла {fname} в папке {raw_dir}.")
    try:
        df = pd.read_csv(path, dtype=str, encoding="utf-8")
    except (UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise DataError(f"Файл {fname} не читается как CSV (UTF-8, разделитель «,»): {exc}") from exc
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        names = ", ".join(f"«{c}»" for c in missing)
        raise DataError(f"В файле {fname} нет колонки {names}.")
    out = pd.DataFrame(index=df.index)
    for col, (kind, required) in cols.items():
        out[col] = _parse_column(fname, col, df[col], kind, required)
    for col in df.columns:  # лишние колонки сохраняем как есть
        if col not in out.columns:
            out[col] = df[col]
    return out


def load_dataset(raw_dir) -> Dataset:
    """Грузит 6 CSV из `raw_dir`; при ошибке — `DataError` с файлом и колонкой по-русски."""
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        raise DataError(f"Нет папки с данными: {raw_dir}.")
    tables = {f: _read_table(raw_dir, f) for f in SCHEMA}
    if tables["sales"].empty:
        raise DataError("Файл sales_monthly.csv пуст: нет ни одной строки продаж.")
    for f in ("sales", "stock"):
        tables[f] = tables[f].sort_values(["sku_id", "month"], kind="stable").reset_index(drop=True)
    return Dataset(**tables)


def detect_as_of(ds: Dataset, cfg: Config) -> date:
    """Дата анализа: `cfg.as_of`; иначе неполный последний месяц → его `cfg.partial_day`,
    полный → 1-е число следующего месяца."""
    if cfg.as_of is not None:
        return cfg.as_of
    last = ds.sales["month"].max()
    if (ds.sales.loc[ds.sales["month"] == last, "partial_month"] == 1).any():
        return date(last.year, last.month, cfg.partial_day)
    nxt = last + 1
    return date(nxt.year, nxt.month, 1)


def _refresh_sku(sku: pd.DataFrame, sales: pd.DataFrame, po: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Пересчитывает поля активности справочника по обрезанным данным так, как их считает ERP
    (сверено с полной выгрузкой: наценка — средняя, взвешенная по количеству, за месяцы
    [месяц даты анализа − 12; дата анализа), «месяц с продажами» — orders_cnt > 0)."""
    sku = sku.copy()
    month = pd.Period(as_of, "M")
    active = sales[sales["orders_cnt"] > 0].groupby("sku_id")
    win = sales[(sales["month"] >= month - 12) & sales["markup_pct"].notna()]
    w_qty = win.groupby("sku_id")["qty_sold"].sum()
    w_mk = (win["markup_pct"] * win["qty_sold"]).groupby(win["sku_id"]).sum()
    markup = (w_mk / w_qty.where(w_qty > 0)).round(0)
    open_po = po[po["status"] == "open"].groupby("sku_id")["qty_pending"].sum()
    ids = sku["sku_id"]
    sku["first_sale_month"] = ids.map(sales.groupby("sku_id")["month"].min())
    sku["last_sale_month"] = ids.map(active["month"].max())
    sku["months_with_sales"] = ids.map(active.size()).fillna(0).astype("int64")
    sku["qty_total"] = ids.map(sales.groupby("sku_id")["qty_sold"].sum()).fillna(0).astype("int64")
    sku["avg_markup_pct_12m"] = ids.map(markup).astype("float64")
    sku["on_order_qty"] = ids.map(open_po).fillna(0).astype("int64")
    return sku


def cut_dataset(ds: Dataset, as_of: date, partial_day: int | None = None) -> Dataset:
    """Набор «как он выглядел бы» на `as_of` — для прошлого прогона и бэктеста.

    Продажи месяцев раньше месяца `as_of` (все полные); продажи месяца `as_of` до этой даты —
    доля прошедших дней (D13), строкой неполного месяца (`partial_month = 1`): прогноз её не
    берёт, а остаток на дату (`stock_on`) вычитает из снимка на 1-е. Если в выгрузке этот месяц
    сам неполный (выгрузка на `partial_day`-е), доля считается от его `partial_day − 1` дней.
    Снимки остатка на 1-е ≤ `as_of`, приходы раньше `as_of`, заказы с `order_date < as_of`.
    Заказ с плановой датой ≥ `as_of` на ту дату считается ещё в пути (приближение):
    `qty_pending += qty_received`, `qty_received = 0`, `status = open`. Карточки, заведённые
    после `as_of` и без продаж до неё, убираются; поля активности справочника пересчитываются.
    """
    month = pd.Period(as_of, "M")
    sales = ds.sales[ds.sales["month"] < month].copy()
    sales["partial_month"] = 0
    cur = ds.sales[ds.sales["month"] == month].copy()
    if as_of.day > 1 and not cur.empty:
        days = np.where(cur["partial_month"] == 1, max((partial_day or as_of.day) - 1, 1),
                        calendar.monthrange(as_of.year, as_of.month)[1])
        share = np.minimum((as_of.day - 1) / days, 1.0)
        for col in ("qty_sold", "qty_returned", "orders_cnt"):
            cur[col] = np.rint(cur[col].to_numpy() * share).astype("int64")
        cur["partial_month"] = 1
        sales = pd.concat([sales, cur], ignore_index=True).sort_values(["sku_id", "month"], kind="stable")
    stock = ds.stock[ds.stock["month"] <= month].copy()
    receipts = ds.receipts[ds.receipts["receipt_date"] < as_of].copy()
    po = ds.po[ds.po["order_date"] < as_of].copy()
    future = po["promised_date"] >= as_of
    po.loc[future, "qty_pending"] = po.loc[future, "qty_pending"] + po.loc[future, "qty_received"]
    po.loc[future, "qty_received"] = 0
    po.loc[future, "status"] = "open"
    sku = ds.sku
    born_later = (sku["created"].notna() & (sku["created"] > month)
                  & ~sku["sku_id"].isin(sales["sku_id"]))
    sku = _refresh_sku(sku[~born_later], sales, po, as_of)
    return replace(ds, sku=sku.reset_index(drop=True), sales=sales.reset_index(drop=True),
                   stock=stock.reset_index(drop=True), po=po.reset_index(drop=True),
                   receipts=receipts.reset_index(drop=True))
