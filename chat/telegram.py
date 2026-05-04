import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = 'https://api.telegram.org/bot{token}/{method}'
SEND_TIMEOUT = 10

# Regex for extracting user UUID from a forwarded message that the founder replies to.
# Matches the line "👤 USER: <uuid>" — UUID is alphanumeric with optional dashes/underscores.
USER_UUID_RE = re.compile(r'👤\s*USER:\s*([A-Za-z0-9_-]+)')


def format_inbound_for_telegram(user_uuid: str, text: str, metadata: dict, email: str | None) -> str:
    """Build the Telegram-bound text for a user's inbound message."""
    metadata = metadata or {}

    lines = [f"👤 USER: {user_uuid}"]

    device_parts = []
    if metadata.get('app_version'):
        device_parts.append(f"v{metadata['app_version']}")
    if metadata.get('ios_version'):
        device_parts.append(f"iOS {metadata['ios_version']}")
    if metadata.get('device_model'):
        device_parts.append(metadata['device_model'])
    if device_parts:
        lines.append('📱 ' + ' · '.join(device_parts))

    status_parts = []
    if metadata.get('subscription_status'):
        status_parts.append(metadata['subscription_status'])
    if metadata.get('recordings_count') is not None:
        status_parts.append(f"{metadata['recordings_count']} recordings")
    if metadata.get('language'):
        status_parts.append(metadata['language'])
    if status_parts:
        lines.append('💳 ' + ' · '.join(status_parts))

    if email:
        lines.append(f"✉️ {email}")

    lines.append('')
    lines.append('—————')
    lines.append('')
    lines.append(text)

    return '\n'.join(lines)


def send_message(text: str) -> int | None:
    """
    Send a message to the owner's Telegram chat. Returns the Telegram message_id on success,
    or None on failure. Failures are logged; callers should not raise on the result being None.
    """
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_OWNER_CHAT_ID:
        logger.error("Telegram not configured: missing TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_CHAT_ID")
        return None

    url = TELEGRAM_API_BASE.format(token=settings.TELEGRAM_BOT_TOKEN, method='sendMessage')
    payload = {
        'chat_id': settings.TELEGRAM_OWNER_CHAT_ID,
        'text': text,
    }
    try:
        resp = requests.post(url, json=payload, timeout=SEND_TIMEOUT)
        data = resp.json()
    except requests.RequestException as exc:
        logger.error("Telegram sendMessage network error: %s", exc)
        return None
    except ValueError as exc:
        logger.error("Telegram sendMessage returned non-JSON response: %s", exc)
        return None

    if not data.get('ok'):
        logger.error("Telegram sendMessage failed: %s", data)
        return None

    message_id = data.get('result', {}).get('message_id')
    logger.info("Telegram sendMessage ok, message_id=%s", message_id)
    return message_id


def extract_user_uuid_from_replied_text(text: str) -> str | None:
    """Pull the user UUID out of a message that the founder replied to."""
    if not text:
        return None
    match = USER_UUID_RE.search(text)
    if not match:
        return None
    return match.group(1)
