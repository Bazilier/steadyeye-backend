"""Server-side Anthropic proxy for the SteadyEye iOS app.

Not a general-purpose LLM endpoint. The client sends script text and nothing
else: model, system prompt, max_tokens and every limit are fixed here. Any
other field in the request body is ignored on purpose.

    POST /api/v1/ai/optimize/   {"text": "..."}   -> {"text": ..., "remaining_free": int|null}
    POST /api/v1/ai/split/      {"text": "..."}   -> {"text": ..., "remaining_free": null}
    POST /api/v1/ai/draft/      {"text": "<topic>", "length_sec": 15|30|60, "kind": "..."}
                                -> {"text": ..., "remaining_free": int|null}

`draft` is the one endpoint with fields beyond `text`, and both are closed
enums: the server, not the client, turns them into the model's input.

Identity is the `X-App-User-Id` header (the RevenueCat appUserID), checked
against RC for the 'access' entitlement. There is no authentication in front
of it — the header is asserted, not proven — so every quota below is a cost
ceiling, not an identity guarantee. App Attest is the missing piece.
"""

import logging
import re
from datetime import timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_ipv46_address
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.exceptions import ParseError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import AITextLog, AIUsage
from .services.anthropic_client import AnthropicError, create_message
from .services.entitlement import is_paid_user, is_valid_app_user_id
from .services.prompts import (
    DRAFT_SYSTEM_PROMPT,
    OPTIMIZE_SYSTEM_PROMPT,
    SPLIT_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

USER_ID_HEADER = 'X-App-User-Id'

# Per-endpoint constants. `max_tokens` matches what AnthropicService.swift
# sent, so output length is unchanged.
#
# OPTIMIZE input cap: 20000 chars. The client only ever feeds it one script at
# a time (ScriptEditorView, or one item of a split result).
#
# SPLIT input cap: 18000 chars. BulkImportView chunks at 10000 chars only when
# the document exceeds 15000 (BulkImportView.swift:369) — below that threshold
# it posts the whole document in one call, so 15000 is the real largest client
# payload, not the 10000 chunk size. 15000 + 20% = 18000.
#
# DRAFT input cap: 300 chars. The input is a one-line topic, not a script.
ENDPOINTS = {
    'optimize': {
        'system_prompt': OPTIMIZE_SYSTEM_PROMPT,
        'max_tokens': 2048,
        'max_input_chars': 20000,
        'requires_paid': False,
        'free_limit_setting': 'AI_FREE_DAILY_LIMIT',
    },
    'split': {
        'system_prompt': SPLIT_SYSTEM_PROMPT,
        'max_tokens': 8192,
        'max_input_chars': 18000,
        'requires_paid': True,
        'free_limit_setting': None,
    },
    'draft': {
        'system_prompt': DRAFT_SYSTEM_PROMPT,
        'max_tokens': 1024,
        'max_input_chars': 300,
        'requires_paid': False,
        'free_limit_setting': 'AI_FREE_DRAFT_DAILY_LIMIT',
    },
}

# Draft options. Spoken pace is about 2.5 words a second.
DRAFT_LENGTH_WORDS = {15: 40, 30: 80, 60: 150}
DRAFT_DEFAULT_LENGTH = 30
DRAFT_KINDS = {
    'short': 'Short vertical video (Reels, TikTok, Shorts)',
    'youtube': 'YouTube video',
    'sales': 'Sales or pitch video',
    'lesson': 'Lesson or course segment',
    'other': 'Talking-to-camera video',
}
DRAFT_DEFAULT_KIND = 'other'
DRAFT_FALLBACK_LANGUAGE = 'en'
# The model answers this, alone, for a topic it will not write about.
DRAFT_REFUSAL = 'CANNOT_DRAFT'
_LANGUAGE_TAG = re.compile(r'^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$')


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def optimize(request):
    return _handle(request, 'optimize')


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def split(request):
    return _handle(request, 'split')


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([])
def draft(request):
    return _handle(request, 'draft')


def _draft_user_message(request, body: dict, topic: str) -> str | None:
    """The model's input for a draft, built here from closed enums plus the
    topic. None when an option is present but not one of the allowed values.
    """
    length = body.get('length_sec', DRAFT_DEFAULT_LENGTH)
    kind = body.get('kind', DRAFT_DEFAULT_KIND)
    # bool is an int subclass: reject True/False explicitly.
    if isinstance(length, bool) or length not in DRAFT_LENGTH_WORDS:
        return None
    if kind not in DRAFT_KINDS:
        return None
    return (
        f"Topic: {topic.strip()}\n"
        f"Kind of video: {DRAFT_KINDS[kind]}\n"
        f"Target length: {length} seconds, about {DRAFT_LENGTH_WORDS[length]} words\n"
        f"Fallback language: {_fallback_language(request)}"
    )


def _fallback_language(request) -> str:
    """First Accept-Language tag (iOS sends the app's language), if sane."""
    header = request.META.get('HTTP_ACCEPT_LANGUAGE') or ''
    first = header.split(',')[0].split(';')[0].strip()
    return first if _LANGUAGE_TAG.match(first) else DRAFT_FALLBACK_LANGUAGE


def _handle(request, endpoint: str) -> Response:
    config = ENDPOINTS[endpoint]

    # Identity first: without a usable app_user_id there is nothing to
    # attribute an AIUsage row to, so this one rejection is not recorded.
    app_user_id = request.headers.get(USER_ID_HEADER)
    if not is_valid_app_user_id(app_user_id):
        logger.info("ai.%s rejected: invalid_app_user_id", endpoint)
        return Response(
            {'error': 'invalid_app_user_id'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    ip = _client_ip(request)
    ctx = {'app_user_id': app_user_id, 'ip': ip, 'endpoint': endpoint}

    # (a) Kill switch / missing key.
    if not settings.AI_ENABLED or not settings.ANTHROPIC_API_KEY:
        return _reject(ctx, 'ai_disabled', 'ai_disabled', status.HTTP_503_SERVICE_UNAVAILABLE)

    # (b) Input. Anything in the body other than `text` is ignored.
    try:
        body = request.data
    except ParseError:
        body = None
    if not isinstance(body, dict):
        return _reject(ctx, 'invalid_input', 'invalid_input', status.HTTP_400_BAD_REQUEST)

    text = body.get('text')
    if not isinstance(text, str) or not text.strip():
        return _reject(ctx, 'invalid_input', 'invalid_input', status.HTTP_400_BAD_REQUEST)

    input_chars = len(text)
    ctx['input_chars'] = input_chars
    max_chars = config['max_input_chars']
    if input_chars > max_chars:
        return _reject(
            ctx, 'input_too_long', 'input_too_long',
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            extra={'max_chars': max_chars},
        )

    user_message = text
    if endpoint == 'draft':
        user_message = _draft_user_message(request, body, text)
        if user_message is None:
            return _reject(ctx, 'invalid_input', 'invalid_input', status.HTTP_400_BAD_REQUEST)

    now = timezone.now()
    midnight = now.astimezone(dt_timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # (c) Per-IP hourly ceiling. Counts attempts that reached Anthropic
    # (ok + upstream_error); rejections are excluded so a client stuck in a
    # 400 loop can still recover once it sends a valid request.
    if ip is not None:
        ip_recent = (
            AIUsage.objects
            .filter(ip=ip, created_at__gte=now - timedelta(hours=1))
            .exclude(status='rejected')
            .count()
        )
        if ip_recent >= settings.AI_IP_HOURLY_LIMIT:
            return _reject(ctx, 'ip_hourly_limit', 'rate_limited', status.HTTP_429_TOO_MANY_REQUESTS)

    # (d) Global daily ceiling — the hard stop on the monthly bill.
    global_today = AIUsage.objects.filter(status='ok', created_at__gte=midnight).count()
    if global_today >= settings.AI_GLOBAL_DAILY_LIMIT:
        logger.warning("ai.%s: global daily limit reached (%s)", endpoint, global_today)
        return _reject(ctx, 'global_daily_limit', 'rate_limited', status.HTTP_429_TOO_MANY_REQUESTS)

    # (e) Entitlement and per-user quotas.
    is_paid = is_paid_user(app_user_id)
    ctx['is_paid'] = is_paid
    remaining_free = None

    if config['requires_paid'] and not is_paid:
        return _reject(
            ctx, 'subscription_required', 'subscription_required', status.HTTP_403_FORBIDDEN
        )

    if is_paid:
        paid_today = AIUsage.objects.filter(
            app_user_id=app_user_id, status='ok', created_at__gte=midnight
        ).count()
        if paid_today >= settings.AI_PAID_DAILY_LIMIT:
            return _reject(
                ctx, 'paid_daily_limit', 'rate_limited', status.HTTP_429_TOO_MANY_REQUESTS
            )
    else:
        # Same UTC-midnight window as the paid check above, so both tiers
        # roll over at the same instant. Each endpoint open to free users
        # has its own allowance.
        free_limit = getattr(settings, config['free_limit_setting'])
        free_today = AIUsage.objects.filter(
            app_user_id=app_user_id, endpoint=endpoint, status='ok',
            created_at__gte=midnight,
        ).count()
        if free_today >= free_limit:
            return _reject(
                ctx, 'free_quota_exhausted', 'free_quota_exhausted', status.HTTP_403_FORBIDDEN
            )

        global_free_today = AIUsage.objects.filter(
            status='ok', is_paid=False, created_at__gte=midnight
        ).count()
        if global_free_today >= settings.AI_GLOBAL_FREE_DAILY_LIMIT:
            logger.warning(
                "ai.%s: global free daily limit reached (%s)", endpoint, global_free_today
            )
            return _reject(
                ctx, 'global_free_daily_limit', 'rate_limited', status.HTTP_429_TOO_MANY_REQUESTS
            )

        remaining_free = max(0, free_limit - (free_today + 1))

    # (f) Upstream call.
    try:
        result_text, input_tokens, output_tokens = create_message(
            config['system_prompt'], user_message, config['max_tokens']
        )
    except AnthropicError as exc:
        _record(ctx, status_value='upstream_error')
        logger.warning("ai.%s upstream_error: %s", endpoint, exc)
        return Response({'error': 'upstream_error'}, status=status.HTTP_502_BAD_GATEWAY)

    # (g) Success.
    _record(
        ctx,
        status_value='ok',
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    logger.info(
        "ai.%s ok: app_user_id=%s is_paid=%s in_tokens=%s out_tokens=%s",
        endpoint, app_user_id, is_paid, input_tokens, output_tokens,
    )
    _log_text(request, endpoint, is_paid, text, result_text)
    if endpoint == 'draft' and result_text.strip() == DRAFT_REFUSAL:
        # Still counted: the call was made and paid for.
        return Response(
            {'error': 'cannot_draft', 'remaining_free': remaining_free},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return Response({'text': result_text, 'remaining_free': remaining_free})


def _client_ip(request) -> str | None:
    """Last entry of X-Forwarded-For, else REMOTE_ADDR.

    Railway's edge APPENDS the connecting address to whatever XFF the caller
    sent, so the last entry is the one the proxy vouched for and every entry
    before it is attacker-supplied. Reading the first entry would let a client
    pick its own bucket and walk straight past the hourly limit.

    This holds only while exactly one trusted proxy sits in front of the app.
    Add another hop (a CDN, a second Railway service) and the trustworthy
    entry moves left — revisit this function if that ever changes.
    """
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR') or ''
    entries = [part.strip() for part in forwarded.split(',') if part.strip()]

    # Only the final hop is trusted; a malformed one falls through to
    # REMOTE_ADDR rather than to the caller's own earlier entries.
    candidates = entries[-1:] + [(request.META.get('REMOTE_ADDR') or '').strip()]

    for candidate in candidates:
        if not candidate:
            continue
        try:
            validate_ipv46_address(candidate)
        except ValidationError:
            continue
        return candidate
    return None


def _record(ctx: dict, *, status_value: str, reject_reason: str = '',
            input_tokens: int = 0, output_tokens: int = 0) -> None:
    AIUsage.objects.create(
        app_user_id=ctx['app_user_id'],
        ip=ctx.get('ip'),
        endpoint=ctx['endpoint'],
        is_paid=ctx.get('is_paid', False),
        status=status_value,
        reject_reason=reject_reason,
        input_chars=ctx.get('input_chars', 0),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=settings.AI_MODEL,
    )


def _log_text(request, endpoint: str, is_paid: bool, input_text: str, output_text: str) -> None:
    """Store a successful request's texts in AITextLog when AI_LOG_TEXTS is on,
    then prune rows past AI_LOG_RETENTION_DAYS. Never raises: a failure here
    must not change the response, and the texts never go to the logger.
    """
    if not settings.AI_LOG_TEXTS:
        return
    try:
        AITextLog.objects.create(
            endpoint=endpoint,
            is_paid=is_paid,
            input_text=input_text,
            output_text=output_text,
            accept_language=(request.META.get('HTTP_ACCEPT_LANGUAGE') or '')[:64],
            user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:256],
        )
        cutoff = timezone.now() - timedelta(days=settings.AI_LOG_RETENTION_DAYS)
        AITextLog.objects.filter(created_at__lt=cutoff).delete()
    except Exception as exc:
        logger.warning("ai.text_log failed: %s", type(exc).__name__)


def _reject(ctx: dict, reason: str, error_code: str, http_status: int,
            extra: dict | None = None) -> Response:
    _record(ctx, status_value='rejected', reject_reason=reason)
    logger.info(
        "ai.%s rejected: reason=%s app_user_id=%s", ctx['endpoint'], reason, ctx['app_user_id']
    )
    payload = {'error': error_code}
    if extra:
        payload.update(extra)
    return Response(payload, status=http_status)
