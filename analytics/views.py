import logging
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

from .alerts import send_alert
from .models import RcEvent

logger = logging.getLogger(__name__)

VALID_SOURCES = ('asa', 'rc', 'ga4')


@api_view(['POST'])
def refresh(request):
    """Trigger an analytics refresh. Phase 1 stub: validates the token and
    request body, logs the request, and returns a stub response. Real
    refresh logic is wired in later phases."""
    token = request.headers.get('X-Refresh-Token')
    if not settings.ANALYTICS_REFRESH_TOKEN or token != settings.ANALYTICS_REFRESH_TOKEN:
        logger.warning("analytics.refresh called with missing/invalid X-Refresh-Token")
        return Response({'error': 'unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        body = request.data or {}
    except ParseError:
        return Response({'error': 'malformed_body'}, status=status.HTTP_400_BAD_REQUEST)

    if not isinstance(body, dict):
        return Response({'error': 'malformed_body'}, status=status.HTTP_400_BAD_REQUEST)

    sources = body.get('sources', list(VALID_SOURCES))
    days = body.get('days', 1)

    if not isinstance(sources, list) or not all(s in VALID_SOURCES for s in sources):
        return Response({'error': 'malformed_body'}, status=status.HTTP_400_BAD_REQUEST)
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        return Response({'error': 'malformed_body'}, status=status.HTTP_400_BAD_REQUEST)

    logger.info("analytics.refresh requested: sources=%s days=%s", sources, days)
    return Response({'status': 'stub', 'phase': 1})


@api_view(['POST'])
def revenuecat_webhook(request):
    """Receive a RevenueCat webhook: verify the shared-secret bearer token,
    persist the event idempotently into RcEvent, and fire a Telegram alert
    for important event types. Responds fast — RC retries on timeout."""
    # 1. Verify the Authorization header matches REVENUECAT_WEBHOOK_SECRET.
    auth_header = request.headers.get('Authorization', '')
    expected = f"Bearer {settings.REVENUECAT_WEBHOOK_SECRET}"

    if not settings.REVENUECAT_WEBHOOK_SECRET:
        logger.error("REVENUECAT_WEBHOOK_SECRET not configured")
        return Response(
            {'error': 'misconfigured'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if auth_header != expected:
        logger.warning("RC webhook: unauthorized request")
        return Response({'error': 'unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

    # 2. Parse the body.
    try:
        body = request.data
        event = body.get('event', {})
        event_id = event.get('id')
        event_type = event.get('type')
    except Exception:  # noqa: BLE001 — malformed JSON / unexpected shape
        return Response({'error': 'malformed_body'}, status=status.HTTP_400_BAD_REQUEST)

    if not event_id or not event_type:
        return Response(
            {'error': 'missing_event_fields'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # 3. Idempotent insert into RcEvent, keyed on RC's event id.
    try:
        RcEvent.objects.update_or_create(
            event_id=event_id,
            defaults={
                'event_type': event_type,
                'occurred_at': datetime.fromtimestamp(
                    event.get('event_timestamp_ms', 0) / 1000, tz=dt_timezone.utc
                ),
                'app_user_id': event.get('app_user_id', ''),
                'product_id': event.get('product_id'),
                'country': event.get('country_code'),
                'price_usd': event.get('price_in_purchased_currency'),
                'currency': event.get('currency'),
                'is_trial_conversion': event.get('is_trial_conversion', False),
                'is_renewal': event_type == 'RENEWAL',
                'raw_payload': body,
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to save RC event %s", event_id)
        return Response({'error': 'db_error'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    logger.info("RC webhook stored event_id=%s type=%s", event_id, event_type)

    # 4. Telegram alerts for important events.
    #
    # Guarded deliberately. The event is ALREADY STORED by this point, so the
    # request has earned its 2xx. A non-2xx would make RevenueCat retry, which
    # re-enters this function and duplicates the Telegram message (the DB write
    # is idempotent on event_id; the alert is not). `send_alert` swallows send
    # failures itself, but only `requests.RequestException` — anything else
    # raised while building or sending would otherwise escape into a 500.
    try:
        _maybe_send_alert(event_type, event)
    except Exception:  # noqa: BLE001 — an alert must never fail the webhook
        logger.exception("Failed to send RC alert for event %s", event_id)

    # 5. Return 200 fast.
    return Response({'status': 'ok', 'event_id': event_id, 'event_type': event_type})


def _format_amount(price, currency: str, price_usd) -> str:
    """Render a purchase amount for a Telegram alert.

    `price_in_purchased_currency` is the amount in the CUSTOMER'S currency, so
    it must never be printed under a bare "$" — a Polish customer paying
    59.99 PLN was being announced as "$59.99", roughly four times the real
    figure. The local amount is labelled with the ISO code (no symbol table)
    and RC's converted figure follows in parentheses:

        59.99 PLN (~$15.20)

    Every fallback below prefers an UNLABELLED number to a wrongly labelled
    one, so a missing field can never turn a foreign amount into dollars.
    """
    def num(value):
        if value is None:
            return None
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)

    local = num(price)
    usd = num(price_usd)

    # No amount at all: say so rather than printing "None" or a bare symbol.
    if local is None:
        return 'unknown'
    # Currency unknown: a bare number is honest, "$" or "USD" would be a guess.
    if not currency:
        return f"{local} (~${usd})" if usd else local
    # Already USD — the parenthetical would just repeat the same figure.
    if currency == 'USD':
        return f"{local} USD"
    # Converted figure missing: the local amount alone, correctly labelled.
    if usd is None:
        return f"{local} {currency}"
    return f"{local} {currency} (~${usd})"


def _maybe_send_alert(event_type: str, event: dict) -> None:
    """Send Telegram alerts for important RC events."""
    raw_user_id = event.get('app_user_id') or 'unknown'
    app_user_id = raw_user_id[:8] + '...'  # truncate for privacy
    product = event.get('product_id', 'unknown')
    country = event.get('country_code', '?')
    price = event.get('price_in_purchased_currency')
    currency = (event.get('currency') or '').upper()
    # RC documents `price` as the amount converted to the account's reporting
    # currency (USD here). This backend had never read it, so it is consumed
    # defensively: if it is absent the alert falls back to the local amount
    # alone rather than inventing a dollar figure.
    price_usd = event.get('price')
    amount = _format_amount(price, currency, price_usd)

    # Trial lifecycle events are NOT distinct RC event types, so the dispatch
    # below cannot key on `event_type` alone:
    #   - trial start      -> INITIAL_PURCHASE with period_type == TRIAL
    #   - trial -> paid    -> RENEWAL with is_trial_conversion == true
    #   - cancel in trial  -> CANCELLATION with period_type == TRIAL
    # INITIAL_PURCHASE and CANCELLATION therefore have to test period_type
    # BEFORE falling through to the paid-path alerts. Without that split a
    # trial start announces itself as a new paying customer for $0 — RC sets
    # the price to zero for a trial — which is exactly what was happening
    # before these branches existed.
    period_type = (event.get('period_type') or '').upper()
    is_trial_period = period_type == 'TRIAL'
    is_trial_conversion = bool(event.get('is_trial_conversion'))

    # Sandbox events are alerted on, never filtered — sandbox is how this gets
    # verified. The marker LEADS the message so it survives truncation in a
    # notification-list preview, where a sandbox purchase must never be
    # mistaken for a real sale at a glance. Production carries no prefix at
    # all, so every real alert reads byte-for-byte as it always has.
    environment = (event.get('environment') or '').upper()
    prefix = '🧪 SANDBOX 🧪 ' if environment == 'SANDBOX' else ''

    if event_type == 'INITIAL_PURCHASE':
        if is_trial_period:
            send_alert(
                f"{prefix}🆕 Trial started: {country} | {product} | user {app_user_id}",
                severity='info',
            )
        else:
            send_alert(
                f"{prefix}💰 New paying customer! {country} | {product} | {amount}",
                severity='info',
            )
    elif event_type == 'RENEWAL':
        # Plain renewals stay silent (too noisy long-term). The trial->paid
        # conversion is the exception: it is the moment the money arrives.
        if is_trial_conversion:
            send_alert(
                f"{prefix}🎉 Trial converted to paid! {country} | {product} | {amount}",
                severity='info',
            )
    elif event_type == 'CANCELLATION':
        if is_trial_period:
            send_alert(
                f"{prefix}💔 Trial cancelled before conversion: {country} | {product} | user {app_user_id}",
                severity='warning',
            )
        else:
            send_alert(
                f"{prefix}❌ Cancellation: {country} | {product} | user {app_user_id}",
                severity='warning',
            )
    elif event_type == 'BILLING_ISSUE':
        send_alert(
            f"{prefix}⚠️ Billing issue: {country} | {product} | user {app_user_id}",
            severity='warning',
        )
    # Every other event type — and a RENEWAL that is not a trial conversion —
    # is stored silently, as before.
