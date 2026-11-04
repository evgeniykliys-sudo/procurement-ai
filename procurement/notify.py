"""Дайджест в Telegram после прогона.

Сейчас — заглушка с финальной сигнатурой: сообщение не отправляется (таск 04 подключит
Bot API `sendMessage`; токен и чат — только из `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def send_digest(plan_json: dict, diff: dict, site_url: str) -> bool:
    """Отправляет дайджест; True — отправлен. Заглушка: всегда False и строка в лог."""
    log.warning("Дайджест в Telegram не отправлен: отправка ещё не подключена.")
    return False
