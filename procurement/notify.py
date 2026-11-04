"""Дайджест в Telegram после прогона: Bot API `sendMessage`, разметка HTML.

Токен и чат — только из `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (окружение или локальный `.env`).
Значение токена не попадает ни в лог, ни в вывод: текст исключений `requests` (в нём URL с
токеном) не выводится, ответ Telegram очищается от токена.
"""
from __future__ import annotations

import html
import logging
from collections import defaultdict
from datetime import date

import requests

from .ai import read_secret

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
TIMEOUT_S = 30
MAX_LEN = 4096  # предел длины сообщения Telegram
TOP_ORDERS = 5
MAX_RISK_SKUS = 8

RISK_LABELS = {
    "deficit_unavoidable": "дефицит неизбежен",
    "deficit_if_late": "дефицит, если опоздать с заказом",
    "excess": "излишек",
    "late_po": "заказ в пути опаздывает",
    "low_reliability": "поставщик недовозит",
}


def _n(value) -> str:
    """Число с пробелом между тысячами: 43066 → «43 066»."""
    if value is None:
        return "—"
    return f"{round(value):,}".replace(",", " ")


def _d(value) -> str:
    """ISO-дата → ДД.ММ.ГГГГ."""
    if not value:
        return "—"
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return html.escape(str(value))


def build_digest(plan_json: dict, diff: dict, site_url: str) -> str:
    """Текст дайджеста (HTML Telegram): KPI, топ-5 к заказу, новые риски, изменения, ссылка."""
    e = html.escape
    k = plan_json.get("kpi", {})
    lines = [f"<b>План закупок · данные на {_d(plan_json.get('as_of'))}</b>", ""]
    no_sup = k.get("order_now_no_supplier") or 0
    lines.append(f"К заказу сейчас: <b>{_n(k.get('order_now_positions'))}</b> поз. / "
                 f"<b>{_n(k.get('order_now_qty'))}</b> шт. у {_n(k.get('order_now_suppliers'))} поставщиков"
                 + (f" (+{_n(no_sup)} без поставщика)" if no_sup else ""))
    lines.append(f"Дефицит неизбежен: {_n(k.get('deficit_unavoidable'))} · если опоздать: "
                 f"{_n(k.get('deficit_if_late'))} · излишки: {_n(k.get('excess'))}")
    lines.append(f"На пересмотр цены: {_n(k.get('review_price'))} · мёртвый ассортимент: {_n(k.get('dead'))}")
    late = k.get("late_lines") or 0
    lines.append(f"В пути: {_n(k.get('in_transit_future_qty'))} шт." + (f", опаздывают строк: {_n(late)}" if late else ""))

    orders = sorted((r for r in plan_json.get("items", []) if r.get("status") == "order_now"),
                    key=lambda r: (-(r.get("order_qty") or 0), r.get("sku_id") or ""))[:TOP_ORDERS]
    if orders:
        lines += ["", f"<b>Топ-{len(orders)} к заказу</b>"]
        for i, r in enumerate(orders, 1):
            lines.append(f"{i}. <code>{e(str(r.get('sku_id')))}</code> — {_n(r.get('order_qty'))} шт., "
                         f"{e(str(r.get('supplier_id') or '—'))}, до {_d(r.get('order_by'))}")

    diff = diff or {}
    risks: dict[str, list[str]] = defaultdict(list)
    for row in diff.get("new_risks") or []:
        risks[row.get("sku_id")].append(RISK_LABELS.get(row.get("risk"), str(row.get("risk"))))
    if risks:
        lines += ["", f"<b>Новые риски ({_n(len(risks))} SKU)</b>"]
        for sku, labels in list(risks.items())[:MAX_RISK_SKUS]:
            lines.append(f"• <code>{e(str(sku))}</code> — {e(', '.join(labels))}")
        if len(risks) > MAX_RISK_SKUS:
            lines.append(f"…и ещё {_n(len(risks) - MAX_RISK_SKUS)}")

    if diff.get("prev_as_of"):
        pct = (plan_json.get("config") or {}).get("change_threshold_pct")
        thr = f" больше чем на {_n(pct)}%" if pct else ""
        lines += ["", f"С прогона на {_d(diff['prev_as_of'])}: новых заказов {_n(len(diff.get('new_orders') or []))}, "
                      f"снято {_n(len(diff.get('dropped_orders') or []))}, "
                      f"количество изменилось{thr} у {_n(len(diff.get('changed') or []))}"]
    if site_url:
        lines += ["", f'<a href="{e(site_url, quote=True)}">Открыть дашборд</a>']
    text = "\n".join(lines)
    return text if len(text) <= MAX_LEN else text[:MAX_LEN - 1] + "…"


def send_digest(plan_json: dict, diff: dict, site_url: str) -> bool:
    """Отправляет дайджест; True — Telegram принял сообщение. Без токена/чата — False и строка в лог."""
    token, chat_id = read_secret("TELEGRAM_BOT_TOKEN"), read_secret("TELEGRAM_CHAT_ID")
    missing = [name for name, v in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat_id)) if not v]
    if missing:
        log.warning("Дайджест в Telegram пропущен: не задано %s.", ", ".join(missing))
        return False
    site_url = site_url or read_secret("SITE_URL")
    body = {"chat_id": chat_id, "text": build_digest(plan_json, diff, site_url), "parse_mode": "HTML",
            "disable_web_page_preview": True}
    try:
        resp = requests.post(f"{API}/bot{token}/sendMessage", json=body, timeout=TIMEOUT_S)
    except requests.Timeout:
        log.warning("Дайджест в Telegram не отправлен: Telegram не ответил за %d с.", TIMEOUT_S)
        return False
    except requests.RequestException as exc:  # текст исключения содержит URL с токеном — не выводим
        log.warning("Дайджест в Telegram не отправлен: нет связи с Telegram (%s).", type(exc).__name__)
        return False
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    if resp.status_code >= 400 or not payload.get("ok"):
        reason = str(payload.get("description") or "").replace(token, "***")[:200]
        log.warning("Дайджест в Telegram не отправлен: HTTP %s%s.", resp.status_code,
                    f" — {reason}" if reason else "")
        return False
    log.info("Дайджест отправлен в Telegram.")
    return True
