"""
Attribution gateway: server-side proxy for the RevenueCat REST API.

The iOS client cannot read RC subscriber attributes directly — RC's iOS SDK
only exposes setters. The secret REST API key required for reads must not
ship in the app bundle, so the iOS app calls this endpoint with its
`app_user_id`; we make the authenticated call to RC and return only the
ASA-relevant fields.

ENDPOINT
    POST /api/v1/attribution/fetch/

REQUEST BODY (JSON)
    { "app_user_id": "uuid-string-from-ios" }

RESPONSES
    200 OK — happy path or "no subscriber yet" (RC 404):
        {
          "traffic_source": "asa" | "unknown",
          "campaign":  "string" | null,
          "ad_group":  "string" | null,
          "keyword":   "string" | null
        }

    400 Bad Request — missing or non-UUID `app_user_id`:
        { "error": "invalid_app_user_id" }

    502 Bad Gateway — RevenueCat returned a non-{200, 404} status,
                      or the request failed/timed out:
        { "error": "upstream_error" }

AUTH
    None. Matches the existing chat endpoint pattern, which trusts the
    client-supplied UUID. The endpoint is read-only and returns no PII
    beyond the four ASA fields, so worst-case abuse is enumeration of
    those four values for known UUIDs.
    TODO: a future iteration could require a shared-secret header
    (`X-Steadyeye-Client-Token`) signed/rotated alongside an iOS build,
    or a per-install bearer token issued by another endpoint.
"""

import logging
import re
import uuid as uuid_module
from urllib.parse import quote

from django.conf import settings
import requests
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

logger = logging.getLogger(__name__)

REVENUECAT_API_BASE = 'https://api.revenuecat.com/v1/subscribers/{app_user_id}'
REVENUECAT_TIMEOUT_SECONDS = 5

# RevenueCat's auto-generated anonymous identifier format. Used when the
# iOS app calls `Purchases.configure(...)` without explicit `logIn(...)` —
# RC mints an ID like `$RCAnonymousID:abc123def456` (hex suffix). We accept
# this format alongside strict UUIDs so the endpoint works whether the
# iOS-side migration to UUID-as-appUserID has happened yet or not.
RC_ANONYMOUS_ID_PATTERN = re.compile(r'^\$RCAnonymousID:[a-f0-9]+$', re.IGNORECASE)


def _is_valid_app_user_id(value: str) -> bool:
    """Accept either a strict UUID (Apple's uppercase hyphenated
    `uuidString` is a subset of what `uuid.UUID()` recognizes) OR
    RevenueCat's auto-generated anonymous ID format
    `$RCAnonymousID:<hex>`. Anything else is rejected.
    """
    if not isinstance(value, str) or not value:
        return False
    if RC_ANONYMOUS_ID_PATTERN.match(value):
        return True
    try:
        uuid_module.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _extract_attribute_value(attributes: dict, key: str):
    """RC stores each attribute as `{value: str, updated_at_ms: int}`.
    Treat missing keys, non-dict shapes, and empty strings as absent.
    """
    obj = attributes.get(key)
    if not isinstance(obj, dict):
        return None
    val = obj.get('value')
    if isinstance(val, str) and val.strip():
        return val
    return None


def _empty_response_payload() -> dict:
    return {
        'traffic_source': 'unknown',
        'campaign': None,
        'ad_group': None,
        'keyword': None,
    }


@api_view(['POST'])
def fetch_attribution(request):
    app_user_id = (request.data or {}).get('app_user_id') if hasattr(request, 'data') else None

    if not _is_valid_app_user_id(app_user_id):
        logger.info("attribution.fetch rejected: invalid_app_user_id")
        return Response(
            {'error': 'invalid_app_user_id'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    api_key = getattr(settings, 'REVENUECAT_SECRET_API_KEY', '') or ''
    if not api_key:
        logger.error("attribution.fetch: REVENUECAT_SECRET_API_KEY is not configured")
        return Response(
            {'error': 'upstream_error'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    # RC's REST path requires URL-encoding of `$` and `:` from the
    # anonymous ID format. UUIDs are URL-safe and pass through unchanged.
    encoded_app_user_id = quote(app_user_id, safe='')
    url = REVENUECAT_API_BASE.format(app_user_id=encoded_app_user_id)
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Accept': 'application/json',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=REVENUECAT_TIMEOUT_SECONDS)
    except requests.RequestException:
        logger.exception("attribution.fetch: RevenueCat request failed for app_user_id=%s", app_user_id)
        return Response(
            {'error': 'upstream_error'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if resp.status_code == 404:
        # Subscriber doesn't exist on RC yet — happens for fresh installs
        # whose first RC call hasn't been received. Not an error.
        logger.info(
            "attribution.fetch: app_user_id=%s rc_status=404 traffic_source=unknown",
            app_user_id,
        )
        return Response(_empty_response_payload())

    if resp.status_code != 200:
        # Defensive against logging full bodies (may carry email if set on
        # the subscriber record). Log status only.
        logger.warning(
            "attribution.fetch: RevenueCat returned non-200 for app_user_id=%s rc_status=%s",
            app_user_id, resp.status_code,
        )
        return Response(
            {'error': 'upstream_error'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    try:
        body = resp.json()
    except ValueError:
        logger.warning(
            "attribution.fetch: RevenueCat returned non-JSON for app_user_id=%s",
            app_user_id,
        )
        return Response(
            {'error': 'upstream_error'},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    attrs = (body.get('subscriber') or {}).get('subscriber_attributes') or {}

    media_source = _extract_attribute_value(attrs, '$mediaSource')
    campaign = _extract_attribute_value(attrs, '$campaign')
    ad_group = _extract_attribute_value(attrs, '$adGroup')
    keyword = _extract_attribute_value(attrs, '$keyword')

    traffic_source = 'asa' if media_source == 'Apple Search Ads' else 'unknown'

    payload = {
        'traffic_source': traffic_source,
        'campaign': campaign,
        'ad_group': ad_group,
        'keyword': keyword,
    }

    logger.info(
        "attribution.fetch: app_user_id=%s rc_status=200 traffic_source=%s",
        app_user_id, traffic_source,
    )
    return Response(payload)
