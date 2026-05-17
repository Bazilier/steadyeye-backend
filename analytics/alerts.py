import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def send_alert(message: str, severity: str = "info") -> None:
    """
    Send an alert to the Telegram owner chat.
    Severity levels: info, warning, error.
    Fails silently if Telegram is misconfigured — alerts must never crash the caller.
    """
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or ''
    chat_id = getattr(settings, 'TELEGRAM_OWNER_CHAT_ID', '') or ''

    if not bot_token or not chat_id:
        logger.warning("Telegram alerts not configured, skipping alert: %s", message)
        return

    emoji = {'info': 'ℹ️', 'warning': '⚠️', 'error': '🔴'}.get(severity, 'ℹ️')
    formatted = f"{emoji} [Analytics] {message}"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    try:
        response = requests.post(
            url,
            json={'chat_id': chat_id, 'text': formatted},
            timeout=5,
        )
        if response.status_code != 200:
            logger.warning("Telegram alert returned %s: %s", response.status_code, response.text)
    except requests.RequestException:
        logger.exception("Failed to send Telegram alert")
