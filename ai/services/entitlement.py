"""Is this app_user_id a paying subscriber?

Answers from RevenueCat, read through EntitlementCache with a 10-minute TTL.
Reuses the REVENUECAT_SECRET_API_KEY already configured for the attribution
gateway — this is a second reader of the same RC data, not a new integration.
"""

import logging
from datetime import timedelta, timezone as dt_timezone
from urllib.parse import quote

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

# The app_user_id validator already shipped for the attribution gateway
# (strict UUID or RevenueCat's `$RCAnonymousID:<hex>`). Imported rather than
# copied so the two endpoints can never drift apart on what they accept.
from attribution.views import _is_valid_app_user_id as is_valid_app_user_id

from ..models import EntitlementCache

logger = logging.getLogger(__name__)

REVENUECAT_API_BASE = 'https://api.revenuecat.com/v1/subscribers/{app_user_id}'
REVENUECAT_TIMEOUT_SECONDS = 5
ENTITLEMENT_ID = 'access'
CACHE_TTL = timedelta(minutes=10)

__all__ = ['is_valid_app_user_id', 'is_paid_user', 'ENTITLEMENT_ID', 'CACHE_TTL']


def is_paid_user(app_user_id: str) -> bool:
    """True when the 'access' entitlement is active (or lifetime).

    ONLY POSITIVE RESULTS ARE CACHED. Caching a negative meant a user who
    subscribed seconds after a free check kept being told they were free for
    up to ten minutes — they had just paid, so that is the one direction this
    must never get wrong. A "not paid" answer therefore always costs an RC
    round trip, which is the cheap side of the trade.

    A fresh cached True short-circuits. Anything else (no row, a legacy
    is_paid=False row, an expired row) queries RC. If RC fails, a cached True
    wins even when stale; with nothing cached the user is treated as free,
    which only ever costs them access, never money.
    """
    now = timezone.now()
    cached = EntitlementCache.objects.filter(app_user_id=app_user_id).first()

    if cached and cached.is_paid and now - cached.checked_at < CACHE_TTL:
        return True

    try:
        is_paid = _fetch_from_revenuecat(app_user_id)
    except _RevenueCatUnavailable:
        if cached and cached.is_paid:
            logger.warning(
                "entitlement: RC unavailable, using stale cache for app_user_id=%s (checked_at=%s)",
                app_user_id, cached.checked_at,
            )
            return True
        logger.warning(
            "entitlement: RC unavailable and no usable cache for app_user_id=%s, treating as free",
            app_user_id,
        )
        return False

    if is_paid:
        EntitlementCache.objects.update_or_create(
            app_user_id=app_user_id,
            defaults={'is_paid': True, 'checked_at': now},
        )
    else:
        # RC is authoritative that this user is not paid. Drop any row so a
        # lapsed subscriber can never be served a stale True from it later.
        # Unconditional rather than guarded on the row we read at the top:
        # that also clears a row a concurrent request wrote in between.
        EntitlementCache.objects.filter(app_user_id=app_user_id).delete()

    return is_paid


class _RevenueCatUnavailable(Exception):
    """RC could not be reached or answered with something unusable."""


def _fetch_from_revenuecat(app_user_id: str) -> bool:
    api_key = getattr(settings, 'REVENUECAT_SECRET_API_KEY', '') or ''
    if not api_key:
        logger.error("entitlement: REVENUECAT_SECRET_API_KEY is not configured")
        raise _RevenueCatUnavailable()

    url = REVENUECAT_API_BASE.format(app_user_id=quote(app_user_id, safe=''))
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Accept': 'application/json',
    }

    try:
        resp = requests.get(url, headers=headers, timeout=REVENUECAT_TIMEOUT_SECONDS)
    except requests.RequestException:
        logger.warning("entitlement: RC request failed for app_user_id=%s", app_user_id)
        raise _RevenueCatUnavailable()

    if resp.status_code == 404:
        # Subscriber unknown to RC — a fresh install that has never purchased.
        # A definite "free", not an outage, so it is not an error path.
        return False

    # 201: RC creates the subscriber record on a GET for an id it has not seen
    # before and answers 201 with the same body shape as 200. It is a normal
    # first-look response, not an error.
    if resp.status_code not in (200, 201):
        logger.warning(
            "entitlement: RC returned %s for app_user_id=%s", resp.status_code, app_user_id
        )
        raise _RevenueCatUnavailable()

    try:
        body = resp.json()
    except ValueError:
        logger.warning("entitlement: RC returned non-JSON for app_user_id=%s", app_user_id)
        raise _RevenueCatUnavailable()

    entitlements = (body.get('subscriber') or {}).get('entitlements') or {}
    entitlement = entitlements.get(ENTITLEMENT_ID)
    if not isinstance(entitlement, dict):
        return False

    return _is_active(entitlement.get('expires_date'))


def _is_active(expires_date) -> bool:
    """A null `expires_date` is RC's lifetime purchase. Otherwise it must be
    in the future. An unparseable value is treated as expired rather than
    granting access on a string we don't understand.
    """
    if expires_date is None:
        return True
    if not isinstance(expires_date, str):
        return False

    parsed = parse_datetime(expires_date)
    if parsed is None:
        logger.warning("entitlement: unparseable expires_date")
        return False
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed > timezone.now()
