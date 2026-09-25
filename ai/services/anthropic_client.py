"""Minimal Anthropic Messages API client built on `requests`.

Deliberately not the anthropic SDK: one POST, one response shape. The request
body mirrors what AnthropicService.swift sent (top-level `system` string, a
single user message) so the model sees exactly the same input as before.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
ANTHROPIC_VERSION = '2023-06-01'
TIMEOUT_SECONDS = 90


class AnthropicError(Exception):
    """Any failure that should surface to the client as 502 upstream_error."""


def create_message(system_prompt: str, user_text: str, max_tokens: int) -> tuple[str, int, int]:
    """Call Anthropic and return (text, input_tokens, output_tokens).

    `model` and `max_tokens` are caller-supplied constants, never client input.
    Raises AnthropicError on timeout, transport failure, non-200, or a body
    that doesn't parse.
    """
    headers = {
        'x-api-key': settings.ANTHROPIC_API_KEY,
        'anthropic-version': ANTHROPIC_VERSION,
        'content-type': 'application/json',
    }
    payload = {
        'model': settings.AI_MODEL,
        'max_tokens': max_tokens,
        'system': system_prompt,
        'messages': [{'role': 'user', 'content': user_text}],
    }

    try:
        resp = requests.post(
            ANTHROPIC_API_URL, headers=headers, json=payload, timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException as exc:
        # `exc` carries the request URL and headers in some cases, so log the
        # class name only — never the exception repr, never the key.
        logger.error("anthropic: request failed (%s)", type(exc).__name__)
        raise AnthropicError('request_failed') from exc

    if resp.status_code != 200:
        logger.warning(
            "anthropic: status=%s error_type=%s",
            resp.status_code, _error_type(resp),
        )
        raise AnthropicError(f'status_{resp.status_code}')

    try:
        body = resp.json()
    except ValueError as exc:
        logger.warning("anthropic: non-JSON body, status=%s", resp.status_code)
        raise AnthropicError('bad_body') from exc

    text = ''.join(
        block.get('text') or ''
        for block in (body.get('content') or [])
        if isinstance(block, dict) and block.get('type') == 'text'
    )
    if not text.strip():
        logger.warning("anthropic: empty text in response, stop_reason=%s", body.get('stop_reason'))
        raise AnthropicError('empty_response')

    usage = body.get('usage') or {}
    return text, int(usage.get('input_tokens') or 0), int(usage.get('output_tokens') or 0)


def _error_type(resp) -> str:
    """Anthropic's `{"error": {"type": ...}}` discriminator, for logs only.

    Never returns the message body — a 400 can echo part of the prompt back.
    """
    try:
        return ((resp.json() or {}).get('error') or {}).get('type') or 'unknown'
    except ValueError:
        return 'unparseable'
