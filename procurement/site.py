"""Статический дашборд в `site/`.

`build_site` собирает `index.html` (шаблон Jinja2 `templates/index.html.j2`), `data/*.json` (выход
расчёта как есть), `data/data.js` (те же данные для страницы — так она открывается и с `file://`,
и с GitHub Pages), `exports/` (Excel/CSV заказов, openpyxl) и `assets/` (ECharts, стили и скрипт
страницы из `static/`). Сеть странице не нужна. Числа здесь только раскладываются по таблицам —
считает их `plan.py`.
"""
from __future__ import annotations

import csv
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .labels import CATEGORY_LABELS, RISK_LABELS, STATUS_LABELS

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"
NBSP = " "
NO_SUPPLIER = "—"
NO_DATA = "нет данных"  # доля исполнения поставщика не посчитана: ни один приход не сопоставлен с заказами
TOP_TREND_N = 10
TOP_TREND_MIN_HISTORY = 24  # тренд = последние 12 мес / предыдущие 12 — нужен 2-летний ряд

LEAD_SOURCE_LABELS = {"sku": "по истории SKU", "supplier": "по поставщику", "default": "оценочное"}
MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь",
             "октябрь", "ноябрь", "декабрь"]
DEAD = ("dead_sell", "dead_close", "no_demand")

# Параметры config.toml для «Методики»: ключ → (группа, что значит). Ключ без описания всё равно
# попадает в таблицу — с пометкой «без описания».
PARAM_DOCS = {
    "min_markup_pct": ("Рентабельность", "Минимальная наценка к себестоимости за 12 мес, %. Ниже — не в заказ, а на пересмотр цены"),
    "dead_months": ("Рентабельность", "Мёртвый ассортимент: нет продаж столько месяцев"),
    "default_cover_days": ("Политика заказа", "Покрытие R, дн., если в ERP target_cover_days = 0 (сейчас у всех SKU)"),
    "review_period_days": ("Политика заказа", "Период пересмотра плана, дн.: «заказать сейчас» — всё, что иначе пришлось бы заказывать до следующего пересмотра"),
    "safety_cap_months": ("Политика заказа", "Потолок страхового запаса, мес. спроса периода покрытия (того, на который приходит заказ)"),
    "service_level": ("Политика заказа", "Уровень сервиса по классу ABC — задаёт z страхового запаса"),
    "abc_thresholds": ("Политика заказа", "ABC по количеству продаж за 12 мес внутри категории: A — первые 80%, B — до 95%, C — остальное"),
    "excess_cover_months": ("Политика заказа", "Излишек: остаток + в пути покрывают больше стольких месяцев прогноза; заказ не больше прогноза за этот срок"),
    "stockout_min_lost_qty": ("Политика заказа", "Товар считается кончившимся, когда неудовлетворённый спрос дошёл до стольких штук"),
    "min_order_qty": ("Политика заказа", "Минимальный заказ, шт.: потребность меньше — не заказываем"),
    "lot_rounding": ("Политика заказа", "Шаг округления кратности партии по медиане прихода SKU"),
    "lot_max_overage_months": ("Политика заказа", "До типичной партии округляем, только если это добавляет не больше стольких месяцев спроса; иначе — шагом"),
    "lead_default_days": ("Плечо поставки", "Плечо, дн., если нет истории ни по SKU, ни по поставщику"),
    "lead_min_days": ("Плечо поставки", "Пол плеча, дн."),
    "lead_max_days": ("Плечо поставки", "Потолок плеча, дн."),
    "overdue_grace_days": ("Открытые заказы", "Просроченный заказ ещё считается «в пути», если просрочка не больше, дн.; больше — «уточнить у поставщика»"),
    "overdue_eta_days": ("Открытые заказы", "Через сколько дней после даты анализа ждём просроченный заказ"),
    "reliability_min_ratio": ("Надёжность поставщика", "Ниже этой доли исполнения заказ делится на неё (поправка на недовоз)"),
    "reliability_max_uplift": ("Надёжность поставщика", "Поправка на недовоз не больше этого множителя"),
    "reliability_min_age_days": ("Надёжность поставщика", "Учитываются закрытые/исполненные заказы старше стольких дней"),
    "reliability_receipt_window_days": ("Надёжность поставщика", "Приход засчитывается заказу, если пришёл до плановой даты + N дн."),
    "trend_clip": ("Прогноз спроса", "Границы тренда (продажи последних 12 мес / предыдущих 12)"),
    "trend_damping": ("Прогноз спроса", "Затухание тренда: прогноз = уровень × тренд^N (1 — целиком, 0 — без тренда); 0,5 — по бэктесту"),
    "backtest_months": ("Прогноз спроса", "Бэктест: срез на столько месяцев раньше даты анализа, прогноз сверяется с фактом этих месяцев"),
    "new_sku_months": ("Прогноз спроса", "Новинка: меньше стольких полных месяцев истории → флаг «мало истории»"),
    "shrink_min_months": ("Прогноз спроса", "Свой сезонный профиль SKU смешивается с профилем категории при истории не меньше, мес."),
    "season_own_weight": ("Прогноз спроса", "Вес своего профиля в смеси (0 — только категория, 1 — только свой)"),
    "season_outlier_factor": ("Прогноз спроса", "Год, где месяц отличается от медианы по годам больше чем во столько раз, обрезается (разовая отгрузка — не сезон)"),
    "level_min_season_index": ("Прогноз спроса", "Видны только месяцы не сезона (средний индекс ниже порога) → уровень по 24 мес / без сезонной поправки"),
    "forecast_cap_vs_last_year": ("Прогноз спроса", "Прогноз на год не выше N× продаж последних 12 мес (флаг «упущенный спрос — проверить»)"),
    "forecast_months": ("Прогноз спроса", "Горизонт прогноза и проекции остатка, мес."),
    "partial_day": ("Прогноз спроса", "День выгрузки неполного месяца: дата анализа = этот день последнего месяца продаж"),
    "as_of": ("Прогноз спроса", "Дата анализа вручную; не задана — по данным"),
    "change_threshold_pct": ("«Что изменилось»", "Изменение потребности больше стольких % попадает в список изменений"),
    "llm_model": ("ИИ-аналитик", "Модель DeepSeek для записки (ключ — только в переменной DEEPSEEK_API_KEY)"),
    "llm_base_url": ("ИИ-аналитик", "Адрес OpenAI-совместимого API"),
    "llm_timeout_s": ("ИИ-аналитик", "Таймаут запроса, с."),
    "site_url": ("Публикация", "Адрес дашборда (GitHub Pages) для дайджеста"),
}


# --- форматирование ---------------------------------------------------------------------------

def fmt_int(v, dash="—") -> str:
    """Целое с неразрывным пробелом между тысячами: 43066 → «43 066»."""
    if v is None or v == "":
        return dash
    return f"{round(float(v)):,}".replace(",", NBSP).replace("-", "−")


def fmt_num(v, digits=1, dash="—") -> str:
    if v is None:
        return dash
    return f"{float(v):,.{digits}f}".replace(",", NBSP).replace(".", ",").replace("-", "−")


def fmt_date(v, dash="—") -> str:
    """ISO-дата (или дата-время) → ДД.ММ.ГГГГ."""
    if not v:
        return dash
    d = v if isinstance(v, date) else date.fromisoformat(str(v)[:10])
    return d.strftime("%d.%m.%Y")


def fmt_datetime(v) -> str:
    if not v:
        return "—"
    return datetime.fromisoformat(str(v)).strftime("%d.%m.%Y %H:%M")


def fmt_pct(v, digits=0, signed=False, dash="—") -> str:
    """Доля → проценты: 0.564 → «56%»; строка (например, «нет данных») — как есть."""
    if v is None:
        return dash
    if isinstance(v, str):
        return v
    x = float(v) * 100
    s = f"{x:+.{digits}f}" if signed else f"{x:.{digits}f}"
    return s.replace("-", "−").replace(".", ",") + "%"


def fmt_month(m: str) -> str:
    """«2026-08» → «август 2026»."""
    y, mm = str(m)[:7].split("-")
    return f"{MONTHS_RU[int(mm) - 1]} {y}"


def category_label(c) -> str:
    return CATEGORY_LABELS.get(c, c or "—")


# --- безопасный markdown ---------------------------------------------------------------------

def _inline(s: str) -> str:
    s = escape(s, quote=True)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"<em>\1</em>", s)
    return s


def render_markdown(text: str) -> str:
    """Простой markdown → HTML: заголовки, абзацы, списки, **жирный**, *курсив*, `код`.
    Весь исходный текст экранируется, сырой HTML не проходит. `#` → h3, `##` → h4, глубже → h5."""
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    list_tag = None

    def flush():
        nonlocal list_tag
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()
        if items:
            out.append(f"<{list_tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{list_tag}>")
            items.clear()
            list_tag = None

    for raw in (text or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        h = re.match(r"^\s*(#{1,6})\s+(.*?)\s*#*$", line)
        ul = re.match(r"^\s*[-*•+]\s+(.*)$", line)
        ol = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if h:
            flush()
            level = min(len(h.group(1)) + 2, 5)
            out.append(f"<h{level}>{_inline(h.group(2))}</h{level}>")
        elif ul or ol:
            tag = "ul" if ul else "ol"
            if para or (list_tag and list_tag != tag):
                flush()
            list_tag = tag
            items.append((ul or ol).group(1))
        elif items and raw.startswith(("  ", "\t")):
            items[-1] += " " + line.strip()  # продолжение пункта списка
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return "\n".join(out)


# --- модель страницы -------------------------------------------------------------------------

def _slug(s: str) -> str:
    return "no_supplier" if s == NO_SUPPLIER else re.sub(r"[^A-Za-z0-9_-]+", "_", s)


def export_name(supplier_id: str) -> str:
    """Имя файла заказа поставщику в `exports/`."""
    return f"orders_{_slug(supplier_id)}.xlsx"


def weight_summary(weights: list) -> str:
    """Итог веса заказа: «X кг» / «≥ X кг, без учёта N поз.» / «вес не заведён»."""
    known = [w for w in weights if w is not None]
    missing = len(weights) - len(known)
    if not known:
        return "вес не заведён"
    if missing:
        return f"≥{NBSP}{fmt_int(sum(known))} кг, без учёта {missing} поз."
    return f"{fmt_int(sum(known))} кг"


def _supplier_groups(plan: dict, items_by_id: dict, reliability: dict) -> list[dict]:
    """Заказы «сейчас» по поставщикам: позиции, итоги, вес, даты размещения и прихода."""
    groups: dict[str, dict] = {}
    for o in plan.get("supplier_orders", []):
        g = groups.setdefault(o["supplier_id"], {"supplier_id": o["supplier_id"],
                                                 "label": o.get("supplier_label") or o["supplier_id"],
                                                 "rows": []})
        g["rows"].append({**o, "item": items_by_id.get(o["sku_id"], {})})
    out = []
    for g in groups.values():
        rows = g["rows"]
        rel = reliability.get(g["supplier_id"])
        g.update(
            positions=len(rows), qty=sum(r["order_qty"] for r in rows),
            weight=weight_summary([r["weight_kg"] for r in rows]),
            place_by=min(r["order_by"] for r in rows), place_last=max(r["order_by"] for r in rows),
            arrival_first=min(r["expected_arrival"] for r in rows),
            arrival_last=max(r["expected_arrival"] for r in rows),
            deficit=sum(1 for r in rows if "deficit_unavoidable" in (r["item"].get("risk") or [])),
            # заказы были, а сопоставленных приходов нет — «нет данных», а не 0%
            fill_ratio=(rel["fill_ratio"] if rel["fill_ratio"] is not None
                        else NO_DATA if rel.get("orders_n") else None) if rel else None,
            low_reliability=bool(rel and rel["uplift"] and rel["uplift"] > 1.0),
            file=export_name(g["supplier_id"]), anchor="sup-" + _slug(g["supplier_id"]),
        )
        out.append(g)
    out.sort(key=lambda g: (g["supplier_id"] == NO_SUPPLIER, g["place_by"], -g["qty"]))
    return out


def _trend_tops(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Топ растущих и падающих по тренду среди живых SKU с 2-летней историей."""
    pool = [r for r in items if r["status"] not in DEAD and (r.get("history_months") or 0) >= TOP_TREND_MIN_HISTORY
            and (r.get("sales_12m") or 0) > 0 and r.get("trend") is not None]
    up = sorted((r for r in pool if r["trend"] > 1.0), key=lambda r: (-r["trend"], -r["sales_12m"]))
    down = sorted((r for r in pool if r["trend"] < 1.0), key=lambda r: (r["trend"], -r["sales_12m"]))
    return up[:TOP_TREND_N], down[:TOP_TREND_N]


def _param_value(key, v) -> str:
    if key == "service_level" and isinstance(v, dict):
        return " · ".join(f"{k} {fmt_pct(x)}" for k, x in v.items())
    if key == "lot_rounding" and isinstance(v, list):
        return "; ".join(f"иначе → {step}" if str(lim) == "inf" else f"< {fmt_int(lim)} шт. → {step}"
                         for lim, step in v)
    if key == "abc_thresholds" and isinstance(v, list):
        return " / ".join(fmt_pct(x) for x in v)
    if isinstance(v, list):
        return "[" + "; ".join(str(x).replace(".", ",") for x in v) + "]"
    if v is None:
        return "не задана (по данным)" if key == "as_of" else "не задано"
    if v == "":
        return "не задан"
    if isinstance(v, float):
        return str(v).replace(".", ",")
    return str(v)


def _params(config: dict) -> list[dict]:
    rows = []
    for key, v in config.items():
        group, doc = PARAM_DOCS.get(key, ("Прочее", "без описания"))
        rows.append({"key": key, "group": group, "doc": doc, "value": _param_value(key, v)})
    order = list(dict.fromkeys(g for g, _ in PARAM_DOCS.values())) + ["Прочее"]
    rows.sort(key=lambda r: order.index(r["group"]))
    return rows


def _diff_view(diff: dict) -> dict:
    diff = diff or {}
    risks = Counter(r["risk"] for r in diff.get("new_risks", []))
    changed = sorted(diff.get("changed", []), key=lambda r: -abs(r.get("pct") or 0))
    return {
        "prev_as_of": diff.get("prev_as_of"),
        "new_orders": sorted(diff.get("new_orders", []), key=lambda r: -(r.get("qty") or 0)),
        "dropped_orders": diff.get("dropped_orders", []),
        "changed": changed,
        "changed_up": sum(1 for r in changed if (r.get("pct") or 0) > 0),
        "changed_down": sum(1 for r in changed if (r.get("pct") or 0) < 0),
        "new_risks": [{"risk": k, "label": RISK_LABELS.get(k, k), "n": n} for k, n in risks.most_common()],
        "new_risks_n": len(diff.get("new_risks", [])),
    }


def _accuracy(backtest: dict) -> dict:
    """«Точнее» — по ошибке на уровне SKU (заказ решается по SKU); по категории — для сведения.
    Нет продаж в окне бэктеста — метрик нет: `better = None` («нет продаж»), ни в победы, ни в поражения."""
    def better(r):
        m, nv = r.get("wape_sku_model"), r.get("wape_sku_naive")
        if m is not None and nv is not None:
            return m < nv
        m, nv = r.get("wape_model"), r.get("wape_naive")
        return m <= nv if m is not None and nv is not None else None
    rows = [{**r, "label": category_label(r["category"]), "better": better(r)}
            for r in backtest.get("by_category", [])]
    rows.sort(key=lambda r: (r.get("wape_sku_naive") or 0) - (r.get("wape_sku_model") or 0), reverse=True)
    return {"rows": rows, "total": backtest.get("total") or {}, "cut": backtest.get("cut"),
            "months": backtest.get("months"),
            "wins": [r["label"] for r in rows if r["better"]],
            "losses": [r["label"] for r in rows if r["better"] is False]}


def _view(plan: dict, diff: dict, backtest: dict, note: dict, history_index: list) -> dict:
    items = plan.get("items", [])
    items_by_id = {r["sku_id"]: r for r in items}
    reliability = {r["supplier_id"]: r for r in plan.get("reliability", [])}
    cfg = plan.get("config", {})
    by_status = defaultdict(list)
    for r in items:
        by_status[r["status"]].append(r)
    with_risk = lambda name: [r for r in items if name in (r.get("risk") or [])]
    has_flag = lambda name: sum(1 for r in items if name in (r.get("flags") or []))

    review = sorted(by_status["review_price"],
                    key=lambda r: ("скоро кончится" not in r["flags"], -(r.get("sales_12m") or 0)))
    groups = _supplier_groups(plan, items_by_id, reliability)
    calendar = plan.get("calendar", [])
    note = note or {}
    up, down = _trend_tops(items)
    return {
        "kpi": plan.get("kpi", {}), "cfg": cfg, "as_of": plan.get("as_of"),
        "generated_at": plan.get("generated_at"), "skus_total": len(items),
        "categories": sorted({r["category"] for r in items}, key=category_label),
        "groups": groups,
        "no_supplier": next((g for g in groups if g["supplier_id"] == NO_SUPPLIER), None),
        "suppliers_known": [g for g in groups if g["supplier_id"] != NO_SUPPLIER],
        "arrival_first": min((g["arrival_first"] for g in groups), default=None),
        "arrival_last": max((g["arrival_last"] for g in groups), default=None),
        "later": sorted(by_status["order_later"], key=lambda r: (r["order_by"] or "", -(r["order_qty"] or 0))),
        "review": review,
        "review_low": [r for r in review if "низкая наценка" in r["flags"]],
        "review_no_cost": [r for r in review if "нет себестоимости" in r["flags"]],
        "review_running_out": [r for r in review if "скоро кончится" in r["flags"]],
        "dead_sell": sorted(by_status["dead_sell"], key=lambda r: -(r["stock_now"] or 0)),
        "dead_close": sorted(by_status["dead_close"], key=lambda r: (category_label(r["category"]), r["sku_id"])),
        "deficit": sorted(with_risk("deficit_unavoidable"), key=lambda r: -(r["explain"].get("lost_lead") or 0)),
        "if_late": sorted(with_risk("deficit_if_late"), key=lambda r: (r["order_by"] is None, r["order_by"] or "")),
        "excess": sorted(with_risk("excess"), key=lambda r: -(r["explain"].get("excess_qty") or 0)),
        "late_arrivals": [a for a in plan.get("arrivals", []) if a.get("late")],
        "stale": plan.get("stale_po", []),
        "low_rel": sorted((r for r in plan.get("reliability", []) if r["fill_ratio"] is not None
                           and r["fill_ratio"] < cfg.get("reliability_min_ratio", 0.95)),
                          key=lambda r: r["fill_ratio"]),
        "up": up, "down": down,
        "calendar": calendar, "cal_max": max([m["qty"] for m in calendar] + [1]),
        "diff": _diff_view(diff), "history": history_index or [],
        "note": note, "note_html": render_markdown(note.get("markdown", "")) if note.get("ok") else "",
        "accuracy": _accuracy(backtest or {}),
        "params": _params(cfg),
        "limits": {
            "weight_known": sum(1 for r in items if r.get("weight_kg") is not None),
            "unknown_supply": sum(1 for r in items if r.get("supply_type") == "unknown"),
            "no_supplier": sum(1 for r in items if not r.get("supplier_id")),
            "lead_estimated": has_flag("плечо оценочное"),
            "few_history": has_flag("мало истории"),
            "capped": has_flag("упущенный спрос — проверить"),
            "no_stock_year": has_flag("весь год без остатка"),
        },
    }


def _client_data(plan: dict, backtest: dict, note: dict, history_index: list) -> dict:
    """Данные для скрипта страницы (`data/data.js`): всё, что рисуется в браузере."""
    arrivals = defaultdict(list)
    for a in plan.get("arrivals", []):
        arrivals[a["sku_id"]].append(a)
    return {
        "as_of": plan.get("as_of"), "generated_at": plan.get("generated_at"), "kpi": plan.get("kpi", {}),
        "items": plan.get("items", []), "series": plan.get("series", {}), "arrivals": arrivals,
        "calendar": plan.get("calendar", []),
        "backtest": {"series": (backtest or {}).get("series", {}), "cut": (backtest or {}).get("cut")},
        "sku_notes": (note or {}).get("sku_notes") or {}, "note_model": (note or {}).get("model") or "",
        "config": plan.get("config", {}), "history": history_index or [],
        "labels": {"status": STATUS_LABELS, "risk": RISK_LABELS, "lead_source": LEAD_SOURCE_LABELS,
                   "category": CATEGORY_LABELS},
    }


# --- выгрузки --------------------------------------------------------------------------------

ORDER_COLUMNS = [("Поставщик", 22), ("SKU", 14), ("Категория", 26), ("ABC", 6), ("Кол-во, шт.", 12),
                 ("Кратность, шт.", 13), ("Вес, кг", 16), ("Разместить до", 14), ("Ожидаемый приход", 16),
                 ("Остаток сейчас, шт.", 14), ("В пути, шт.", 11), ("Хватит до", 12), ("Риски", 30)]
HEADER_FILL = PatternFill("solid", fgColor="E8EDF4")
DATE_FMT = "DD.MM.YYYY"
INT_FMT = "#,##0"


def _d(v):
    return date.fromisoformat(v) if v else None


def _order_row(o: dict, it: dict) -> list:
    return [o.get("supplier_label") or o["supplier_id"], o["sku_id"], category_label(it.get("category")),
            it.get("abc"), int(o["order_qty"]), (it.get("explain") or {}).get("lot"),
            o["weight_kg"] if o["weight_kg"] is not None else "вес не заведён",
            _d(o["order_by"]), _d(o["expected_arrival"]), it.get("stock_now"), it.get("in_transit_qty"),
            _d(it.get("stockout_date")), ", ".join(RISK_LABELS.get(x, x) for x in it.get("risk") or [])]


def _sheet(ws, header: list[tuple[str, int]], rows: list[list], date_cols=(), int_cols=()):
    ws.append([h for h, _ in header])
    for i, (_, w) in enumerate(header, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(row=1, column=i)
        c.font, c.fill = Font(bold=True), HEADER_FILL
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if c.column in date_cols and isinstance(c.value, date):
                c.number_format = DATE_FMT
            elif c.column in int_cols and isinstance(c.value, (int, float)):
                c.number_format = INT_FMT
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(header))}{len(rows) + 1}"


def _orders_book(path: Path, orders: list[dict], items_by_id: dict, as_of: str, title: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Заказ"
    rows = [_order_row(o, items_by_id.get(o["sku_id"], {})) for o in orders]
    _sheet(ws, ORDER_COLUMNS, rows, date_cols=(8, 9, 12), int_cols=(5, 6, 7, 10, 11))
    ws.append([])
    ws.append(["Итого", f"{len(rows)} поз.", None, None, sum(o["order_qty"] for o in orders), None,
               weight_summary([o["weight_kg"] for o in orders]).replace(NBSP, " ")])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    ws.cell(row=ws.max_row, column=5).number_format = INT_FMT
    info = wb.create_sheet("О файле")
    for line in (title, f"Данные на {fmt_date(as_of)}",
                 "Позиции со статусом «Заказать сейчас»; количество уже округлено до кратности партии.",
                 "Ожидаемый приход = дата размещения + плечо поставки SKU.",
                 "Вес строки = вес единицы × количество; у большинства SKU вес в ERP не заведён."):
        info.append([line])
    info.column_dimensions["A"].width = 90
    wb.save(path)


def _plan_book(path: Path, items: list[dict], forecast_months: int):
    wb = Workbook()
    ws = wb.active
    ws.title = "План по SKU"
    header = [("SKU", 14), ("Категория", 26), ("Поставщик", 14), ("ABC", 6), ("Статус", 18),
              ("К заказу, шт.", 12), ("Заказать до", 13), ("Остаток, шт.", 11), ("В пути, шт.", 11),
              ("Ближайший приход", 15), ("Хватит до", 12), ("Дней покрытия", 12), ("Плечо, дн.", 10),
              ("Источник плеча", 16), ("Страховой запас, шт.", 14), ("Продажи 12 мес, шт.", 14),
              ("Заказов 12 мес", 12), ("Возвраты 12 мес, шт.", 14), ("Ранг в категории", 12), ("Тренд", 8),
              ("Наценка 12 мес, %", 12), ("Риски", 30), ("Флаги", 40)]
    rows = []
    for r in items:
        cover = r["cover_days"] if r["cover_days"] is not None else (
            None if r["status"] in DEAD else f"> {forecast_months} мес")
        rows.append([r["sku_id"], category_label(r["category"]), r["supplier_id"] or "не заведён", r["abc"],
                     STATUS_LABELS.get(r["status"], r["status"]), r["order_qty"], _d(r["order_by"]),
                     r["stock_now"], r["in_transit_qty"], _d(r["next_eta"]), _d(r["stockout_date"]), cover,
                     r["lead_days"], LEAD_SOURCE_LABELS.get(r["lead_source"], r["lead_source"]),
                     r["safety_stock"], r["sales_12m"], r["orders_12m"], r["returns_12m"],
                     r["rank_in_category"], r["trend"], r["markup_12m"],
                     ", ".join(RISK_LABELS.get(x, x) for x in r["risk"] or []), ", ".join(r["flags"] or [])])
    _sheet(ws, header, rows, date_cols=(7, 10, 11), int_cols=(6, 8, 9, 15, 16, 17, 18))
    wb.save(path)


def write_exports(plan: dict, exports_dir: Path) -> list[str]:
    """`orders_<поставщик>.xlsx`, `orders_all.xlsx`, `orders_all.csv`, `plan_all.xlsx`; имена файлов."""
    exports_dir.mkdir(parents=True, exist_ok=True)
    items_by_id = {r["sku_id"]: r for r in plan.get("items", [])}
    orders = plan.get("supplier_orders", [])
    as_of = plan.get("as_of")
    by_sup = defaultdict(list)
    for o in orders:
        by_sup[o["supplier_id"]].append(o)
    names = []
    for sup, rows in by_sup.items():
        name = export_name(sup)
        _orders_book(exports_dir / name, rows, items_by_id, as_of,
                     f"Заказ поставщику: {rows[0].get('supplier_label') or sup}")
        names.append(name)
    _orders_book(exports_dir / "orders_all.xlsx", orders, items_by_id, as_of, "Заказы всем поставщикам")
    with open(exports_dir / "orders_all.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")  # «;» и BOM — так CSV сразу открывается в русском Excel
        w.writerow([h for h, _ in ORDER_COLUMNS])
        for o in orders:
            row = _order_row(o, items_by_id.get(o["sku_id"], {}))
            w.writerow([fmt_date(v, dash="") if isinstance(v, date) else ("" if v is None else v) for v in row])
    _plan_book(exports_dir / "plan_all.xlsx", plan.get("items", []),
               plan.get("config", {}).get("forecast_months", 18))
    return names + ["orders_all.xlsx", "orders_all.csv", "plan_all.xlsx"]


# --- сборка ----------------------------------------------------------------------------------

def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=select_autoescape(["html", "j2"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters.update(int=fmt_int, num=fmt_num, date=fmt_date, datetime=fmt_datetime, pct=fmt_pct,
                       month=fmt_month, cat=category_label)
    env.globals.update(STATUS=STATUS_LABELS, RISK=RISK_LABELS, LEAD=LEAD_SOURCE_LABELS, NO_SUPPLIER=NO_SUPPLIER)
    return env


SITE_MANIFEST = ".procurement-site.json"  # список файлов, записанных прошлой сборкой


def _files_under(out: Path) -> set[str]:
    return {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p.name != SITE_MANIFEST}


def check_site_dir(out: Path) -> list[str]:
    """Файлы прошлой сборки в `out`, которые можно удалить. Папки нет или она пуста — []. Любой файл,
    которого нет в манифесте прошлой сборки (`data/raw/*.csv`, свой логотип в assets, подписанный
    заказ в exports), — ValueError до любой записи и удаления: сборщик удаляет только своё."""
    if not out.exists():
        return []
    if not out.is_dir():
        raise ValueError(f"папка сайта {out.as_posix()} — это файл")
    files = _files_under(out)
    manifest = out / SITE_MANIFEST
    try:
        known = set(json.loads(manifest.read_text(encoding="utf-8"))) if manifest.exists() else set()
    except ValueError:
        known = set()
    foreign = sorted(files - known)
    if foreign:
        raise ValueError(f"в папке сайта {out.as_posix()} есть файлы не из прошлой сборки "
                         f"({', '.join(foreign[:5])}{' …' if len(foreign) > 5 else ''}) — сборщик их не "
                         f"удаляет; удалите папку {out.as_posix()} целиком или укажите другую: --site {out.name}_new")
    return sorted(files & known)


def build_site(plan_json: dict, diff: dict, backtest: dict, note: dict, history_index: list,
               out_dir="site") -> Path:
    """Собирает сайт в `out_dir`; возвращает путь к `index.html`.

    `out_dir` — новая, пустая или папка прошлой сборки: удаляются только файлы из её манифеста
    (`.procurement-site.json`), чужой файл — отказ без изменений (`--site .` из корня проекта иначе
    снёс бы `data/raw` с выгрузкой ERP)."""
    out = Path(out_dir)
    for rel in check_site_dir(out):
        (out / rel).unlink()
    # после проверки и удаления в папке нет ни одного чужого файла: всё, что появится дальше, записано
    # этой сборкой — манифест пишется и при сбое на середине, иначе следующая сборка отказала бы
    # из-за собственного файла (новый поставщик в exports)
    try:
        for sub in ("data", "exports", "assets"):
            (out / sub).mkdir(parents=True, exist_ok=True)
        for name, data in (("plan", plan_json), ("diff", diff), ("backtest", backtest), ("note", note),
                           ("history", history_index)):
            (out / "data" / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        client = json.dumps(_client_data(plan_json, backtest, note, history_index), ensure_ascii=False,
                            separators=(",", ":"))
        (out / "data" / "data.js").write_text(f"window.PROCUREMENT_DATA = {client};\n", encoding="utf-8")
        for f in STATIC.iterdir():
            if f.is_file():
                shutil.copy2(f, out / "assets" / f.name)
        write_exports(plan_json, out / "exports")
        html = _env().get_template("index.html.j2").render(**_view(plan_json, diff, backtest, note, history_index))
        index = out / "index.html"
        index.write_text(html, encoding="utf-8")
    finally:
        if out.is_dir():
            (out / SITE_MANIFEST).write_text(json.dumps(sorted(_files_under(out)), ensure_ascii=False,
                                                        indent=0), encoding="utf-8")
    return index
