"""Общие фикстуры: синтетический мини-набор данных в формате выгрузки ERP.

`make_dataset(...)` пишет CSV во временную папку и грузит их через `load_dataset`,
поэтому синтетика проходит ту же проверку и приведение типов, что и реальные данные.
Аргументы — списки словарей (или DataFrame) с нужными колонками; недостающие
колонки заполняются значениями по умолчанию. `make_dataset.series(...)` — строки
продаж по месяцам подряд.
"""
from __future__ import annotations

import itertools
from pathlib import Path

import pandas as pd
import pytest

from procurement.data import load_dataset

REAL_RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

COLUMNS = {
    "sku_dim.csv": {
        "sku_id": None, "category": "cat_a", "brand_id": "BRD-1", "supplier_id": "SUP-1",
        "supply_type": "import", "uom": "pcs", "weight_kg": None, "warranty_months": 0,
        "target_cover_days": 0, "price_decile": 5, "first_sale_month": "2021-01",
        "last_sale_month": "2021-01", "months_with_sales": 0, "qty_total": 0,
        "avg_markup_pct_12m": 40, "on_order_qty": 0, "created": "2020-01",
    },
    "sales_monthly.csv": {
        "sku_id": None, "category": "cat_a", "month": None, "qty_sold": 0, "qty_returned": 0,
        "orders_cnt": 1, "markup_pct": 40, "partial_month": 0,
    },
    "stock_monthly.csv": {"sku_id": None, "month": None, "stock_qty": 0},
    "purchase_orders.csv": {
        "po_id": None, "sku_id": None, "supplier_id": "SUP-1", "supply_type": "import",
        "order_date": None, "promised_date": None, "planned_lead_days": 120, "status": "closed",
        "qty_ordered": 0, "qty_confirmed": 0, "qty_received": 0, "qty_pending": 0,
    },
    "receipts.csv": {"sku_id": None, "receipt_date": None, "qty": 0, "supplier_id": "SUP-1",
                     "supply_type": "import"},
    "supplier_dim.csv": {
        "supplier_id": None, "supply_type": "import", "receipts_cnt": 0, "skus": 0,
        "median_days_between_receipts": None, "median_planned_lead_days": None,
    },
}


def _frame(rows, defaults: dict) -> pd.DataFrame:
    if isinstance(rows, pd.DataFrame):
        rows = rows.to_dict("records")
    # явно переданный None остаётся пустым значением; не переданная колонка — значение по умолчанию
    return pd.DataFrame([{**defaults, **r} for r in (rows or [])], columns=list(defaults))


class DatasetFactory:
    def __init__(self, base: Path):
        self.base = base
        self._n = itertools.count()

    @staticmethod
    def series(sku_id, start, qtys, category="cat_a", partial_last=False, orders=None):
        """Строки продаж SKU помесячно с `start` (YYYY-MM); последний месяц — неполный по флагу."""
        p0 = pd.Period(start, freq="M")
        rows = []
        for i, q in enumerate(qtys):
            rows.append({
                "sku_id": sku_id, "category": category, "month": str(p0 + i), "qty_sold": q,
                "orders_cnt": (orders[i] if orders is not None else (1 if q > 0 else 0)),
                "partial_month": int(partial_last and i == len(qtys) - 1),
            })
        return rows

    def write(self, skus=None, sales=None, stock=None, po=None, receipts=None, suppliers=None,
              drop: dict | None = None) -> Path:
        """Пишет 6 CSV и возвращает папку; `drop={"файл.csv": [колонки] | None}` убирает колонки/файл."""
        if sales is None and skus is None:  # SKU — из заказов, приходов, остатков
            ids = dict.fromkeys(r["sku_id"] for r in [*(po or []), *(receipts or []), *(stock or [])])
            skus = [{"sku_id": i} for i in ids]
        if sales is None:  # продажи обязательны: по нулевой строке на SKU
            sales = [{"sku_id": r["sku_id"], "category": r.get("category", "cat_a"), "month": "2024-01",
                      "qty_sold": 0, "orders_cnt": 0} for r in (skus or [])]
        sales_df = _frame(sales, COLUMNS["sales_monthly.csv"])
        if skus is None:
            skus = sales_df[["sku_id", "category"]].drop_duplicates().to_dict("records")
        sku_df = _frame(skus, COLUMNS["sku_dim.csv"])
        if suppliers is None:
            suppliers = [{"supplier_id": s} for s in sku_df["supplier_id"].dropna().unique()]
        frames = {
            "sku_dim.csv": sku_df,
            "sales_monthly.csv": sales_df,
            "stock_monthly.csv": _frame(stock, COLUMNS["stock_monthly.csv"]),
            "purchase_orders.csv": _frame(po, COLUMNS["purchase_orders.csv"]),
            "receipts.csv": _frame(receipts, COLUMNS["receipts.csv"]),
            "supplier_dim.csv": _frame(suppliers, COLUMNS["supplier_dim.csv"]),
        }
        out = self.base / f"raw{next(self._n)}"
        out.mkdir(parents=True)
        for name, df in frames.items():
            cols = (drop or {}).get(name, [])
            if name in (drop or {}) and cols is None:
                continue
            df.drop(columns=cols).to_csv(out / name, index=False)
        return out

    def __call__(self, **kwargs):
        return load_dataset(self.write(**kwargs))


@pytest.fixture
def make_dataset(tmp_path) -> DatasetFactory:
    return DatasetFactory(tmp_path)


@pytest.fixture(scope="session")
def real_ds():
    return load_dataset(REAL_RAW)
