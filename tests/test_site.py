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
    # 7 KPI с разделителями тысяч: 189 поз. / 43 066 шт. / 18 поставщиков / 205 / 84 / 115 / 331
    assert k["order_now_qty"] == 43066
    tiles = re.findall(r'class="kpi-value">([^<]+)<', html)
    assert tiles[:7] == ["189", f"43{NBSP}066", "18", "205", "84", "115", "331"]
    assert "Календарь заказов" in html and "Что изменилось" in html
    assert "Растут" in html and "Падают" in html
    assert "01.07.2026" in html  # сравнение с прошлым прогоном


def test_note_unavailable_shows_reason(run):
    assert "ИИ-записка недоступна: ИИ-записка отключена (--no-ai)" in run["html"]


def test_orders_exports_match_plan(run):
    plan, exp = run["plan"], run["site"] / "exports"
    orders = plan["supplier_orders"]
    wb = load_workbook(exp / "orders_all.xlsx")
    ws = wb.worksheets[0]
    rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r[1] and str(r[1]).startswith("SKU-")]
    assert len(rows) == len(orders) == 189
    assert sum(r[4] for r in rows) == 43066
    with open(exp / "orders_all.csv", encoding="utf-8-sig", newline="") as f:
        csv_rows = [r for r in csv.reader(f, delimiter=";") if r and r[1].startswith("SKU-")]
    assert len(csv_rows) == 189
    for sup in {o["supplier_id"] for o in orders}:
        name = "orders_no_supplier.xlsx" if sup == "—" else f"orders_{sup}.xlsx"
        assert (exp / name).exists(), name
        assert f'href="exports/{name}"' in run["html"], name
    assert (exp / "plan_all.xlsx").exists()
    assert 'href="exports/orders_all.xlsx"' in run["html"] and 'href="exports/orders_all.csv"' in run["html"]


def test_supplier_weight_summary(run):
    html = run["html"]
    # SUP-0F93EC: вес заведён у 2 позиций из 3 (17 580 + 165 кг)
    assert f"≥{NBSP}17{NBSP}745 кг, без учёта 1 поз." in html
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
