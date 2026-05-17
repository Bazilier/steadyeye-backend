"""Client for the RevenueCat REST API v1.

Only the subscriber-read endpoint is used in Phase 2. The v1 secret key
does NOT expose a "list all subscribers" endpoint, so the set of
app_user_ids to snapshot must be accumulated elsewhere (the RcEvent
table, populated by the webhook).
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

RC_API_BASE = 'https://api.revenuecat.com/v1'
RC_TIMEOUT_SECONDS = 10


class RcClientError(Exception):
    """Raised when RC API returns unexpected error."""
    pass


class RcClient:
    """Client for RevenueCat REST API."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.REVENUECAT_SECRET_API_KEY
        if not self.api_key:
            raise ValueError("REVENUECAT_SECRET_API_KEY not configured")

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Accept': 'application/json',
        }

    def fetch_subscriber(self, app_user_id: str) -> dict | None:
        """
        Fetch subscriber data by app_user_id.
        Returns subscriber dict or None if 404.
        Raises RcClientError on other errors.
        """
        url = f'{RC_API_BASE}/subscribers/{requests.utils.quote(app_user_id, safe="")}'
        logger.info("RcClient.fetch_subscriber: GET /v1/subscribers/%s", app_user_id)

        try:
            resp = requests.get(url, headers=self._headers(), timeout=RC_TIMEOUT_SECONDS)
        except requests.RequestException:
            logger.exception("RcClient.fetch_subscriber: network error for app_user_id=%s", app_user_id)
            raise RcClientError("network_error")

        if resp.status_code == 200:
            try:
                body = resp.json()
            except ValueError:
                logger.warning("RcClient.fetch_subscriber: non-JSON 200 for app_user_id=%s", app_user_id)
                raise RcClientError("rc_server_error")
            return body.get('subscriber') or {}

        if resp.status_code == 404:
            logger.info("RcClient.fetch_subscriber: app_user_id=%s not found (404)", app_user_id)
            return None

        if resp.status_code == 401:
            logger.error("RcClient.fetch_subscriber: 401 — invalid API key")
            raise RcClientError("invalid_api_key")

        if resp.status_code == 429:
            logger.warning("RcClient.fetch_subscriber: 429 — rate limited")
            raise RcClientError("rate_limited")

        if 500 <= resp.status_code < 600:
            logger.warning("RcClient.fetch_subscriber: %s — RC server error", resp.status_code)
            raise RcClientError("rc_server_error")

        logger.warning(
            "RcClient.fetch_subscriber: unexpected status %s for app_user_id=%s",
            resp.status_code, app_user_id,
        )
        raise RcClientError("rc_server_error")

    def list_active_subscribers_from_overview(self) -> list[dict]:
        """
        Returns a list of active subscriber summaries.
        Note: RC REST API does not have a native "list all subscribers" endpoint
        for the v1 secret key. The pull_rc command will need to derive the list
        of app_user_ids from another source (e.g. iterating events from RcEvent
        table, or from a future overview endpoint).

        For Phase 2, implemented as a placeholder that returns an empty list
        and logs a warning. The pull_rc command uses individual fetch_subscriber
        calls for known app_user_ids from the RcEvent table.
        """
        logger.warning("list_active_subscribers_from_overview: not natively supported by RC v1 REST API")
        return []
