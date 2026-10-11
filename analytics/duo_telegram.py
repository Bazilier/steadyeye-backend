"""Forward an iPhone Duo diagnostic report to the founder's Telegram.

One Telegram call per report: a single HTML message (header + payload in a
<pre> block) when it fits, otherwise a .json document with the header as its
caption, so nothing is ever truncated. Like `alerts.send_alert`, this never
raises — a Telegram failure must not fail the request that stored the report.
"""

import html
import json
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TELEGRAM_API = 'https://api.telegram.org/bot{token}/{method}'
# (connect, read). Kept short so a Telegram outage can't hold the request.
TELEGRAM_TIMEOUT = (2, 3)
# Telegram's limit for message text. Measured in UTF-16 code units (how
# Telegram counts) over the raw HTML, tags and entities included, which is
# never less than what Telegram counts after parsing.
MESSAGE_LIMIT = 4096


def _utf16_len(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def format_header(report) -> str:
    return (
        f"📱 Duo report #{report.pk} · {report.kind} · {report.model} · "
        f"iOS {report.os_version} · app {report.app_version} ({report.build}) · "
        f"install {str(report.install_id)[:8]}"
    )


def build_message_html(report) -> tuple[str, str]:
    """Return (header, html_message) for the report."""
    header = format_header(report)
    pretty = json.dumps(report.payload, indent=2, ensure_ascii=False)
    message = f"{html.escape(header, quote=False)}\n<pre>{html.escape(pretty, quote=False)}</pre>"
    return header, message


def send_duo_report(report) -> None:
    bot_token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or ''
    chat_id = getattr(settings, 'TELEGRAM_OWNER_CHAT_ID', '') or ''
    if not bot_token or not chat_id:
        logger.warning("Telegram not configured, skipping Duo report #%s", report.pk)
        return

    try:
        header, message = build_message_html(report)
        if _utf16_len(message) <= MESSAGE_LIMIT:
            response = requests.post(
                TELEGRAM_API.format(token=bot_token, method='sendMessage'),
                json={'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML'},
                timeout=TELEGRAM_TIMEOUT,
            )
        else:
            pretty = json.dumps(report.payload, indent=2, ensure_ascii=False)
            filename = f"duo-report-{report.pk}-{report.kind}.json"
            response = requests.post(
                TELEGRAM_API.format(token=bot_token, method='sendDocument'),
                data={'chat_id': chat_id, 'caption': header},
                files={'document': (filename, pretty.encode('utf-8'), 'application/json')},
                timeout=TELEGRAM_TIMEOUT,
            )
        if response.status_code != 200:
            logger.warning(
                "Telegram Duo report #%s returned %s: %s",
                report.pk, response.status_code, response.text[:500],
            )
    except Exception:  # noqa: BLE001 — must never fail the caller
        logger.exception("Failed to send Duo report #%s to Telegram", report.pk)
