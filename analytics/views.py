import logging

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError
from rest_framework.response import Response

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
    """Receive a RevenueCat webhook. Phase 1 stub: validates the bearer
    token, logs the event type, and returns a stub response. Payload
    parsing and RcEvent persistence are wired in Phase 2."""
    auth = request.headers.get('Authorization', '')
    expected = f"Bearer {settings.REVENUECAT_WEBHOOK_SECRET}"
    if not settings.REVENUECAT_WEBHOOK_SECRET or auth != expected:
        logger.warning("revenuecat_webhook called with missing/invalid Authorization header")
        return Response({'error': 'unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        body = request.data or {}
    except ParseError:
        body = {}

    event = body.get('event', {}) if isinstance(body, dict) else {}
    event_type = event.get('type') if isinstance(event, dict) else None
    logger.info("revenuecat_webhook received: event_type=%s", event_type)

    return Response({'status': 'stub', 'phase': 1})
