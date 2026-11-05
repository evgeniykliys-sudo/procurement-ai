from datetime import date

import pandas as pd
import pytest

from procurement.config import Config, load_config
from procurement.data import DataError, cut_dataset, detect_as_of, load_dataset

# --- config -----------------------------------------------------------------

def test_load_config_reads_file_and_defaults_missing_keys(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('min_markup_pct = 30\ntrend_clip = [0.7, 1.3]\nas_of = "2026-07-01"\n'
                 '[service_level]\nA = 0.99\nB = 0.9\nC = 0.8\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg.min_markup_pct == 30
    assert cfg.trend_clip == (0.7, 1.3)
    assert cfg.as_of == date(2026, 7, 1)
    assert cfg.service_level["A"] == 0.99
    assert cfg.lead_default_days == 123  # ключа нет в файле → значение по умолчанию
    assert cfg.abc_thresholds == (0.80, 0.95)


def test_project_config_toml_has_every_config_field():
    import dataclasses
    import tomllib
    from pathlib import Path

    raw = tomllib.loads((Path(__file__).parent.parent / "config.toml").read_text(encoding="utf-8"))
    names = {f.name for f in dataclasses.fields(Config)}
    assert names - set(raw) <= {"as_of"}  # as_of по умолчанию не задан (= авто)
    cfg = load_config(Path(__file__).parent.parent / "config.toml")
    assert cfg.lead_max_days == 180 and cfg.partial_day == 13 and cfg.as_of is None


# --- load_dataset -----------------------------------------------------------

def test_load_real_dataset_all_tables(real_ds):
    assert len(real_ds.sku) == 791
    assert len(real_ds.sales) == 37414
    assert len(real_ds.stock) == 9627
    assert len(real_ds.po) == 660
    assert len(real_ds.receipts) == 2394
    assert len(real_ds.suppliers) == 39
    assert isinstance(real_ds.sales["month"].iloc[0], pd.Period)
    assert isinstance(real_ds.po["order_date"].iloc[0], date)
    assert isinstance(real_ds.receipts["receipt_date"].iloc[0], date)


def test_missing_file_is_reported_in_russian(make_dataset):
    d = make_dataset.write(sales=make_dataset.series("SKU-1", "2024-01", [1, 2]),
                           drop={"receipts.csv": None})
    with pytest.raises(DataError, match="receipts.csv"):
        load_dataset(d)


def test_missing_column_names_file_and_column(make_dataset):
    d = make_dataset.write(sales=make_dataset.series("SKU-1", "2024-01", [1, 2]),
                           drop={"sales_monthly.csv": ["qty_sold"]})
    with pytest.raises(DataError) as e:
        load_dataset(d)
    assert "sales_monthly.csv" in str(e.value) and "qty_sold" in str(e.value)
    assert "нет колонки" in str(e.value)


def test_broken_date_names_file_column_and_value(make_dataset):
    d = make_dataset.write(
        sales=make_dataset.series("SKU-1", "2024-01", [1, 2]),
        receipts=[{"sku_id": "SKU-1", "receipt_date": "2024-13-45", "qty": 5}],
    )
    with pytest.raises(DataError) as e:
        load_dataset(d)
    msg = str(e.value)
    assert "receipts.csv" in msg and "receipt_date" in msg and "2024-13-45" in msg


def test_broken_month_is_reported(make_dataset):
    rows = make_dataset.series("SKU-1", "2024-01", [1, 2])
    rows[1]["month"] = "февраль"
    with pytest.raises(DataError, match="month"):
        load_dataset(make_dataset.write(sales=rows))


# --- detect_as_of -----------------------------------------------------------

def test_detect_as_of_real_data_is_13_aug_2026(real_ds):
    assert detect_as_of(real_ds, Config()) == date(2026, 8, 13)


def test_detect_as_of_full_last_month_gives_first_of_next(make_dataset):
    ds = make_dataset(sales=make_dataset.series("SKU-1", "2024-01", [1, 2, 3]))
    assert detect_as_of(ds, Config()) == date(2024, 4, 1)


def test_detect_as_of_partial_uses_partial_day_and_override(make_dataset):
    ds = make_dataset(sales=make_dataset.series("SKU-1", "2024-01", [1, 2, 3], partial_last=True))
    assert detect_as_of(ds, Config(partial_day=20)) == date(2024, 3, 20)
    assert detect_as_of(ds, Config(as_of=date(2024, 2, 1))) == date(2024, 2, 1)


# --- cut_dataset ------------------------------------------------------------

def test_cut_dataset_real_2026_07_01_is_consistent(real_ds):
    cut_on = date(2026, 7, 1)
    c = cut_dataset(real_ds, cut_on)
    assert c.sales["month"].max() == pd.Period("2026-06", "M")
    assert (c.sales["partial_month"] == 0).all()
    assert c.stock["month"].max() == pd.Period("2026-07", "M")
    assert (c.receipts["receipt_date"] < cut_on).all()
    assert (c.po["order_date"] < cut_on).all()
    fut = c.po[c.po["promised_date"] >= cut_on]
    assert (fut["status"] == "open").all()
    # «в пути» на дату обрезки — заказано минус пришедшее до неё по приходам
    assert (fut["qty_received"] + fut["qty_pending"] == fut["qty_ordered"]).all()
    assert fut["qty_received"].sum() <= c.receipts["qty"].sum()
    # справочник пересчитан на дату: мёртвые = без продаж за 12 мес
    assert c.sku["last_sale_month"].dropna().max() <= pd.Period("2026-06", "M")
    assert (c.sku["on_order_qty"] >= 0).all()
    # исходный набор не изменён
    assert real_ds.sales["month"].max() == pd.Period("2026-08", "M")
    assert detect_as_of(c, Config()) == cut_on


def test_cut_dataset_recomputes_markup_like_erp(make_dataset):
    sales = make_dataset.series("SKU-1", "2024-01", [10] * 12 + [0] * 6)  # 2024-01..2025-06
    for r, m in zip(sales, [10] * 6 + [40] * 6 + [None] * 6):
        r["markup_pct"] = m
    ds = make_dataset(sales=sales)
    assert cut_dataset(ds, date(2025, 1, 1)).sku["avg_markup_pct_12m"].iloc[0] == pytest.approx(25)
    # 2025-07: окно 2024-07..2025-06 → продажи только с наценкой 40
    assert cut_dataset(ds, date(2025, 7, 1)).sku["avg_markup_pct_12m"].iloc[0] == pytest.approx(40)
    # 2026-02: за 12 мес продаж нет → наценка пустая (мёртвый)
    assert pd.isna(cut_dataset(ds, date(2026, 2, 1)).sku["avg_markup_pct_12m"].iloc[0])


def test_partial_service_level_keeps_other_classes(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("[service_level]\nA = 0.99\n", encoding="utf-8")
    assert load_config(p).service_level == {"A": 0.99, "B": 0.90, "C": 0.80}


def test_cut_dataset_mid_month_keeps_sales_to_date(make_dataset):
    """D13: на 16.01 продажи января до этой даты (15 из 31 дня) — строкой неполного месяца:
    остаток на дату их вычитает из снимка на 1-е, прогноз их не берёт."""
    from procurement.demand import stock_on
    stock = [{"sku_id": "SKU-1", "month": "2024-01", "stock_qty": 100}]
    ds = make_dataset(sales=make_dataset.series("SKU-1", "2023-01", [62] * 13), stock=stock)  # январь 2024 полный
    c = cut_dataset(ds, date(2024, 1, 16))
    cur = c.sales[c.sales["month"] == pd.Period("2024-01", "M")]
    assert cur["qty_sold"].tolist() == [30] and cur["partial_month"].tolist() == [1]
    assert stock_on(c, date(2024, 1, 16)).iloc[0] == 70
    assert stock_on(cut_dataset(ds, date(2024, 1, 1)), date(2024, 1, 1)).iloc[0] == 100
    # месяц в выгрузке сам неполный (по 13-е): на 07.01 — 6 из его 12 дней
    part = make_dataset(sales=make_dataset.series("SKU-1", "2023-01", [62] * 12 + [24], partial_last=True), stock=stock)
    c = cut_dataset(part, date(2024, 1, 7), partial_day=13)
    assert c.sales.loc[c.sales["month"] == pd.Period("2024-01", "M"), "qty_sold"].tolist() == [12]


def test_cut_dataset_does_not_take_lead_from_supplier_dim(make_dataset):
    """Справочник поставщиков посчитан по всей выгрузке: на прошлую дату плечо поставщика — только
    из его заказов до неё (у SUP-2 первый заказ позже — плечо по умолчанию, а не 30 дн. из справочника)."""
    from procurement.supply import lead_times
    sales = make_dataset.series("A", "2024-01", [5] * 12) + make_dataset.series("B", "2024-01", [5] * 12)
    skus = [{"sku_id": "A", "supplier_id": "SUP-1"}, {"sku_id": "B", "supplier_id": "SUP-2"}]
    po = [{"po_id": "P1", "sku_id": "A", "supplier_id": "SUP-1", "order_date": "2024-02-01",
           "promised_date": "2024-05-01", "planned_lead_days": 90, "status": "closed", "qty_ordered": 10},
          {"po_id": "P2", "sku_id": "C", "supplier_id": "SUP-2", "order_date": "2024-10-02",
           "promised_date": "2024-11-01", "planned_lead_days": 30, "status": "closed", "qty_ordered": 10}]
    suppliers = [{"supplier_id": "SUP-1", "median_planned_lead_days": 90},
                 {"supplier_id": "SUP-2", "median_planned_lead_days": 30}]
    ds = make_dataset(skus=skus, sales=sales, po=po, suppliers=suppliers)
    cfg = Config()
    full = lead_times(ds, cfg).set_index("sku_id")
    assert full.loc["B", "lead_days"] == 30 and full.loc["B", "lead_source"] == "supplier"
    cut = lead_times(cut_dataset(ds, date(2024, 8, 1)), cfg).set_index("sku_id")
    assert cut.loc["A", "lead_days"] == 90 and cut.loc["A", "lead_source"] == "sku"
    assert cut.loc["B", "lead_source"] == "default" and cut.loc["B", "lead_days"] == cfg.lead_default_days


def test_duplicate_sku_card_is_a_data_error(make_dataset):
    """Две карточки одного SKU — понятная ошибка с файлом и строками, а не падение pandas."""
    raw = make_dataset.write(skus=[{"sku_id": "SKU-A"}, {"sku_id": "SKU-B"}, {"sku_id": "SKU-A"}])
    with pytest.raises(DataError, match=r"sku_dim\.csv.*SKU-A.*строки 2, 4"):
        load_dataset(raw)


def test_partial_day_beyond_month_end_is_clamped(make_dataset):
    """partial_day = 31 у неполного февраля — последний день февраля, а не ValueError."""
    ds = make_dataset(sales=make_dataset.series("SKU-P", "2023-01", [5] * 14, partial_last=True))
    assert detect_as_of(ds, Config(partial_day=31)) == date(2024, 2, 29)


def test_cut_keeps_late_closed_order_in_transit(make_dataset):
    """D18: закрытый заказ с плановой датой до среза, приход по которому пришёл после, на дату среза
    ещё ехал (опаздывал): open, в пути — пришедшее после, получено — до."""
    ds = make_dataset(
        skus=[{"sku_id": "SKU-L"}],
        sales=make_dataset.series("SKU-L", "2024-01", [10] * 6),
        po=[{"po_id": "PO-1", "sku_id": "SKU-L", "order_date": "2024-01-10", "promised_date": "2024-04-01",
             "status": "closed", "qty_ordered": 100, "qty_received": 0},
            {"po_id": "PO-2", "sku_id": "SKU-L", "order_date": "2024-01-05", "promised_date": "2024-03-01",
             "status": "closed", "qty_ordered": 50, "qty_received": 0}],
        receipts=[{"sku_id": "SKU-L", "receipt_date": "2024-03-01", "qty": 50},
                  {"sku_id": "SKU-L", "receipt_date": "2024-03-20", "qty": 30},
                  {"sku_id": "SKU-L", "receipt_date": "2024-05-10", "qty": 70}])
    cut = cut_dataset(ds, date(2024, 5, 1)).po.set_index("po_id")
    assert cut.loc["PO-1", "status"] == "open"
    assert cut.loc["PO-1", "qty_pending"] == 70 and cut.loc["PO-1", "qty_received"] == 30
    assert cut.loc["PO-2", "status"] == "closed"  # получен целиком до среза


def test_cut_dataset_future_order_partly_received_before_cut(make_dataset):
    """Заказ 100 шт. с плановой датой после среза, 60 пришли до среза: в пути 40, не 100."""
    ds = make_dataset(
        skus=[{"sku_id": "SKU-P"}],
        sales=make_dataset.series("SKU-P", "2024-01", [10] * 6),
        po=[{"po_id": "PO-1", "sku_id": "SKU-P", "order_date": "2024-01-10", "promised_date": "2024-06-01",
             "status": "closed", "qty_ordered": 100, "qty_received": 100, "qty_pending": 0}],
        receipts=[{"sku_id": "SKU-P", "receipt_date": "2024-03-01", "qty": 60},
                  {"sku_id": "SKU-P", "receipt_date": "2024-06-01", "qty": 40}])
    po = cut_dataset(ds, date(2024, 4, 1)).po.set_index("po_id")
    assert po.loc["PO-1", "status"] == "open"
    assert po.loc["PO-1", "qty_pending"] == 40 and po.loc["PO-1", "qty_received"] == 60


def test_cut_dataset_future_closed_order_with_zero_qty_fields(make_dataset):
    """Закрытый заказ, полученный после среза, с qty_received = qty_pending = 0 (D01): в пути весь заказ."""
    ds = make_dataset(
        skus=[{"sku_id": "SKU-E"}],
        sales=make_dataset.series("SKU-E", "2024-01", [10] * 6),
        po=[{"po_id": "PO-1", "sku_id": "SKU-E", "order_date": "2024-01-10", "promised_date": "2024-04-10",
             "status": "closed", "qty_ordered": 100, "qty_received": 0, "qty_pending": 0}],
        receipts=[{"sku_id": "SKU-E", "receipt_date": "2024-04-10", "qty": 100}])
    po = cut_dataset(ds, date(2024, 3, 1)).po.set_index("po_id")
    assert po.loc["PO-1", "status"] == "open" and po.loc["PO-1", "qty_pending"] == 100


def test_cut_dataset_before_any_sale_does_not_fail(make_dataset):
    """Срез, на который продаж ещё не было ни у одной карточки: не падает, last_sale_month пуст."""
    ds = make_dataset(skus=[{"sku_id": "SKU-Z"}],
                      sales=make_dataset.series("SKU-Z", "2024-01", [0, 0, 5, 5], orders=[0, 0, 1, 1]))
    c = cut_dataset(ds, date(2024, 2, 1))
    assert c.sku["last_sale_month"].isna().all() and (c.sku["months_with_sales"] == 0).all()


def test_cut_dataset_before_first_sales_month_does_not_fail(make_dataset):
    """Срез раньше первого месяца продаж (бэктест ранней даты): продаж нет вовсе — не падает."""
    ds = make_dataset(skus=[{"sku_id": "SKU-Z"}], sales=make_dataset.series("SKU-Z", "2024-03", [5, 5]))
    c = cut_dataset(ds, date(2024, 1, 1))
    assert c.sku["first_sale_month"].isna().all() and c.sku["last_sale_month"].isna().all()
