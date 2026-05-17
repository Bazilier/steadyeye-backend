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
    _maybe_send_alert(event_type, event)

    # 5. Return 200 fast.
    return Response({'status': 'ok', 'event_id': event_id, 'event_type': event_type})


def _maybe_send_alert(event_type: str, event: dict) -> None:
    """Send Telegram alerts for important RC events."""
    raw_user_id = event.get('app_user_id') or 'unknown'
    app_user_id = raw_user_id[:8] + '...'  # truncate for privacy
    product = event.get('product_id', 'unknown')
    country = event.get('country_code', '?')
    price = event.get('price_in_purchased_currency')

    if event_type == 'INITIAL_PURCHASE':
        send_alert(
            f"💰 New paying customer! {country} | {product} | ${price}",
            severity='info',
        )
    elif event_type == 'CANCELLATION':
        send_alert(
            f"❌ Cancellation: {country} | {product} | user {app_user_id}",
            severity='warning',
        )
    elif event_type == 'BILLING_ISSUE':
        send_alert(
            f"⚠️ Billing issue: {country} | {product} | user {app_user_id}",
            severity='warning',
        )
    # RENEWAL and other event types: no alert (would be too noisy long-term).
