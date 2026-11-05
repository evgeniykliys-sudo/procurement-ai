"""Дашборд: сборка сайта на реальном прогоне — разделы, данные, выгрузки, записка ИИ."""
import csv
import json
import re

import pytest
from openpyxl import load_workbook

from conftest import REAL_RAW
from procurement.__main__ import main
from procurement.site import build_site, render_markdown

NBSP = " "
TABS = ["Сводка", "К заказу", "Все SKU", "Риски", "На пересмотр цены", "Мёртвый ассортимент",
        "Точность прогноза", "Методика и параметры"]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    base = tmp_path_factory.mktemp("site")
    common = ["--raw", str(REAL_RAW), "--history", str(base / "history")]
    assert main(["seed-history", *common]) == 0
    assert main(["run", "--no-ai", *common, "--out", str(base / "out"), "--site", str(base / "site")]) == 0
    load = lambda p: json.loads((base / p).read_text(encoding="utf-8"))
    return {"base": base, "site": base / "site", "plan": load("out/plan.json"), "diff": load("out/diff.json"),
            "backtest": load("out/backtest.json"), "html": (base / "site" / "index.html").read_text(encoding="utf-8")}


def test_sections_assets_and_no_cdn(run):
    html, site = run["html"], run["site"]
    for tab in TABS:
        assert f">{tab}<" in html, tab
    assert (site / "assets" / "echarts.min.js").stat().st_size > 500_000
    for name in ("plan", "diff", "backtest", "note", "history"):
        assert (site / "data" / f"{name}.json").exists(), name
    assert (site / "data" / "data.js").exists()
    # страница не тянет ничего из сети: все скрипты и стили — относительные пути
    for src in re.findall(r'<(?:script|link)[^>]+(?:src|href)="([^"]+)"', html):
        assert not src.startswith(("http:", "https:", "//")), src
        assert (site / src).exists(), src


def test_summary_kpi_and_dates(run):
    html, k = run["html"], run["plan"]["kpi"]
    assert "Данные на 13.08.2026" in html
    assert "пересчитано" in html
    # 7 KPI с разделителями тысяч: 195 поз. / 34 945 шт. / 18 поставщиков / 206 / 82 / 115 / 331
    # (до правок D07–D11 было 42 796 шт.: страховой без сезона, партия до медианы прихода, недовоз
    # по несопоставленным заказам; до D14 — 188 / 34 379: заказ до пересмотра при 0 шт. на сегодня терялся)
    assert k["order_now_qty"] == 34945
    tiles = re.findall(r'class="kpi-value">([^<]+)<', html)
    assert tiles[:7] == ["195", f"34{NBSP}945", "18", "206", "82", "115", "331"]
    assert "127 уже на нуле" in html  # из 206 «дефицит неизбежен» остатка нет уже сейчас
    assert "Календарь заказов" in html and "Что изменилось" in html
    assert "Растут" in html and "Падают" in html
    assert "01.07.2026" in html  # сравнение с прошлым прогоном


def test_site_dir_with_foreign_files_is_refused_and_untouched(tmp_path):
    # `--site .` из корня проекта: сборщик пересоздаёт data/ — data/raw с выгрузкой не должна пропасть
    raw = tmp_path / "data" / "raw"
    raw.mkdir(parents=True)
    (raw / "sku_dim.csv").write_text("sku_id\n", encoding="utf-8")
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="не из прошлой сборки"):
        build_site({}, {}, {}, {}, [], out_dir=tmp_path)
    assert (raw / "sku_dim.csv").exists()
    only_raw = tmp_path / "x"
    (only_raw / "data" / "raw").mkdir(parents=True)
    (only_raw / "data" / "raw" / "x.csv").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="data/raw/x.csv"):
        build_site({}, {}, {}, {}, [], out_dir=only_raw)


def test_note_unavailable_shows_reason(run):
    assert "ИИ-записка недоступна: ИИ-записка отключена (--no-ai)" in run["html"]


def test_orders_exports_match_plan(run):
    plan, exp = run["plan"], run["site"] / "exports"
    orders = plan["supplier_orders"]
    wb = load_workbook(exp / "orders_all.xlsx")
    ws = wb.worksheets[0]
    rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[1] and str(r[1]).startswith("SKU-")]
    assert len(rows) == len(orders) == 195
    assert sum(r[4] for r in rows) == 34945
    with open(exp / "orders_all.csv", encoding="utf-8-sig", newline="") as f:
        csv_rows = [r for r in csv.reader(f, delimiter=";") if r and r[1].startswith("SKU-")]
    assert len(csv_rows) == 195
    for sup in {o["supplier_id"] for o in orders}:
        name = "orders_no_supplier.xlsx" if sup == "—" else f"orders_{sup}.xlsx"
        assert (exp / name).exists(), name
        assert f'href="exports/{name}"' in run["html"], name
    assert (exp / "plan_all.xlsx").exists()
    assert 'href="exports/orders_all.xlsx"' in run["html"] and 'href="exports/orders_all.csv"' in run["html"]


def test_supplier_without_matched_receipts_shows_no_data(run):
    plan, html = run["plan"], run["html"]
    rel = {r["supplier_id"]: r for r in plan["reliability"]}
    no_data = [s for s in {o["supplier_id"] for o in plan["supplier_orders"]}
               if s in rel and rel[s]["orders_n"] > 0 and rel[s]["fill_ratio"] is None]
    assert no_data  # на реальных данных такие поставщики есть среди заказов «сейчас»
    assert "исполнение нет данных" in html
    assert "исполнение 0%" not in html


def test_supplier_weight_summary(run):
    html = run["html"]
    # SUP-0F93EC: вес заведён у 2 позиций из 3 (21 096 + 192.5 кг)
    assert f"≥{NBSP}21{NBSP}288 кг, без учёта 1 поз." in html
    assert "вес не заведён" in html
    assert "поставщик не заведён" in html


def test_methodology_lists_every_config_param(run):
    html = run["html"]
    for key in run["plan"]["config"]:
        assert f"<code>{key}</code>" in html, key
    assert "возвраты" in html.lower() and "не вычитаются" in html
    assert "7 SKU из 791" in html


def test_risk_review_dead_and_accuracy_counts(run):
    html = run["html"]
    assert html.count('data-sku="') >= 115 + 331  # строки пересмотра и мёртвых кликабельны
    assert "Распродать" in html and "Закрыть карточку" in html and "скоро кончится" in html
    assert "WAPE" in html and "Обогреватели" in html


def test_note_markdown_is_rendered_safely(run, tmp_path):
    note = {"ok": True, "model": "deepseek-chat", "error": None,
            "markdown": "## Главное\n- **189** позиций к заказу\n- <script>alert(1)</script>",
            "sku_notes": {"SKU-026A51": "Пик в марте, заказывать сейчас."}}
    build_site(run["plan"], run["diff"], run["backtest"], note, [], out_dir=tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "<strong>189</strong>" in html and "<li>" in html
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "сгенерировано ИИ (deepseek-chat), числа — из расчёта системы" in html
    assert "Пик в марте, заказывать сейчас." in (tmp_path / "data" / "data.js").read_text(encoding="utf-8")


def test_render_markdown_basics():
    out = render_markdown("# Итог\nТекст с `кодом` и *курсивом*.\n\n1. раз\n2. два\n\n<b>x</b> & y")
    assert "<h3>Итог</h3>" in out
    assert "<code>кодом</code>" in out and "<em>курсивом</em>" in out
    assert "<ol><li>раз</li><li>два</li></ol>" in out
    assert "&lt;b&gt;x&lt;/b&gt; &amp; y" in out


def test_accuracy_category_without_sales_does_not_break_site(run, tmp_path):
    """Категория без продаж в окне бэктеста: метрик нет — «нет продаж», а не TypeError."""
    bt = json.loads(json.dumps(run["backtest"]))
    bt["by_category"].append({"category": "empty_cat", "actual": 0.0, "model": 0.0, "naive": 0.0,
                              "wape_model": None, "wape_naive": None, "bias_model": None, "bias_naive": None,
                              "wape_sku_model": None, "wape_sku_naive": None, "excluded_sku_months": 0,
                              "new_sku_actual": 0})
    bt["series"]["empty_cat"] = []
    index = build_site(run["plan"], run["diff"], bt, {"ok": False, "error": "нет"}, [], out_dir=tmp_path / "s")
    html = index.read_text(encoding="utf-8")
    assert "empty_cat" in html and "нет продаж" in html


def test_site_dir_keeps_foreign_files_even_with_site_names(tmp_path):
    """Папка с index.html, data, assets, exports, но файлы не из прошлой сборки — отказ, ничего не
    удалено."""
    site = tmp_path / "site"
    for rel in ("index.html", "data/sku_dim.csv", "assets/company-logo.svg", "exports/signed-order.xlsx"):
        (site / rel).parent.mkdir(parents=True, exist_ok=True)
        (site / rel).write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="не из прошлой сборки"):
        build_site({}, {}, {}, {}, [], out_dir=site)
    for rel in ("index.html", "data/sku_dim.csv", "assets/company-logo.svg", "exports/signed-order.xlsx"):
        assert (site / rel).read_text(encoding="utf-8") == "x", rel


def test_failed_site_check_writes_nothing(tmp_path):
    """Недопустимая папка сайта — отказ до записи истории и out/: план и дашборд не расходятся."""
    bad = tmp_path / "site"
    bad.mkdir()
    (bad / "notes.txt").write_text("x", encoding="utf-8")
    rc = main(["run", "--no-ai", "--raw", str(REAL_RAW), "--history", str(tmp_path / "history"),
               "--out", str(tmp_path / "out"), "--site", str(bad)])
    assert rc == 2
    assert not (tmp_path / "out").exists() and not (tmp_path / "history").exists()


def test_site_rebuild_removes_only_own_files(run, tmp_path):
    """Своя прошлая сборка (манифест) пересобирается: удаляется только записанное ею."""
    import shutil
    site = tmp_path / "site"
    shutil.copytree(run["site"], site)
    (site / "exports" / "orders_OLD-SUP.xlsx").write_bytes(b"")  # выгрузка прошлой сборки
    manifest = site / ".procurement-site.json"
    names = json.loads(manifest.read_text(encoding="utf-8")) + ["exports/orders_OLD-SUP.xlsx"]
    manifest.write_text(json.dumps(names), encoding="utf-8")
    note = {"ok": False, "markdown": "", "sku_notes": {}, "error": "нет", "model": ""}
    build_site(run["plan"], run["diff"], run["backtest"], note, [], out_dir=site)
    assert not (site / "exports" / "orders_OLD-SUP.xlsx").exists() and (site / "index.html").exists()
    (site / "data" / "raw").mkdir()
    (site / "data" / "raw" / "sku_dim.csv").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="data/raw/sku_dim.csv"):
        build_site(run["plan"], run["diff"], run["backtest"], note, [], out_dir=site)
    assert (site / "data" / "raw" / "sku_dim.csv").exists() and (site / "index.html").exists()


def test_site_build_failure_does_not_lock_the_folder(run, tmp_path, monkeypatch):
    """Сбой посреди сборки (после выгрузки нового поставщика): манифест всё равно пишется — повторная
    сборка не отказывает из-за собственного файла; подсказка в отказе — не та же папка."""
    from procurement import site as site_mod
    out = tmp_path / "site"
    note = {"ok": False, "markdown": "", "sku_notes": {}, "error": "нет", "model": ""}
    plan_one = json.loads(json.dumps(run["plan"]))
    first = plan_one["supplier_orders"][0]["supplier_id"]
    plan_one["supplier_orders"] = [o for o in plan_one["supplier_orders"] if o["supplier_id"] == first]
    build_site(plan_one, run["diff"], run["backtest"], note, [], out_dir=out)
    real_env = site_mod._env
    monkeypatch.setattr(site_mod, "_env", lambda: (_ for _ in ()).throw(RuntimeError("сбой шаблона")))
    with pytest.raises(RuntimeError):
        build_site(run["plan"], run["diff"], run["backtest"], note, [], out_dir=out)
    monkeypatch.setattr(site_mod, "_env", real_env)
    build_site(run["plan"], run["diff"], run["backtest"], note, [], out_dir=out)
    assert (out / "index.html").exists()
    (out / "mine.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="site_new") as err:
        build_site(run["plan"], run["diff"], run["backtest"], note, [], out_dir=out)
    assert "--site site " not in str(err.value) + " "
