"""Client for the Apple Ads (Apple Search Ads) Campaign Management API v5.

Auth flow (Apple's OAuth for the Search Ads API):
  1. Build a self-signed ES256 JWT from the team/client/key IDs + PEM.
  2. Exchange the JWT for a short-lived access token at appleid.apple.com.
  3. Cache the access token for 55 minutes and use it as a bearer token.
  4. On a 401, drop the cache, mint a fresh token, and retry once.

The private key and JWT are never logged.
"""

import datetime as _dt  # noqa: F401 — kept for type hints in docstrings
import logging
import time

import jwt
import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

OAUTH_TOKEN_URL = 'https://appleid.apple.com/auth/oauth2/token'
OAUTH_AUDIENCE = 'https://appleid.apple.com'
ASA_API_BASE = 'https://api.searchads.apple.com/api/v5'

ACCESS_TOKEN_CACHE_KEY = 'asa_access_token'
ACCESS_TOKEN_TTL_SECONDS = 55 * 60     # cache the token for 55 min
JWT_LIFETIME_SECONDS = 60 * 60         # JWT valid for 60 min
REQUEST_TIMEOUT_SECONDS = 30
REPORT_PAGE_LIMIT = 1000


class AsaError(Exception):
    """Base class for all ASA client errors."""
    pass


class AsaAuthError(AsaError):
    """OAuth/authentication failure (e.g. 401 after a token refresh)."""
    pass


class AsaRateLimitError(AsaError):
    """ASA API returned 429 Too Many Requests."""
    pass


class AsaClientError(AsaError):
    """ASA API returned a 4xx other than 401/429."""
    pass


class AsaServerError(AsaError):
    """ASA API returned a 5xx."""
    pass


class AsaTimeoutError(AsaError):
    """A request to Apple timed out."""
    pass


# Per-level report endpoint config: (path-suffix, orderBy field).
_LEVEL_ORDER_FIELD = {
    'campaign': 'campaignId',
    'ad_group': 'adGroupId',
    'keyword': 'keywordId',
    'search_term': 'adGroupId',
}


class AsaClient:
    """Client for Apple Ads Campaign Management API v5."""

    def __init__(self):
        self.private_key = settings.ASA_PRIVATE_KEY
        self.client_id = settings.ASA_CLIENT_ID
        self.team_id = settings.ASA_TEAM_ID
        self.key_id = settings.ASA_KEY_ID
        self.org_id = settings.ASA_ORG_ID

        missing = [
            name for name, value in (
                ('ASA_PRIVATE_KEY', self.private_key),
                ('ASA_CLIENT_ID', self.client_id),
                ('ASA_TEAM_ID', self.team_id),
                ('ASA_KEY_ID', self.key_id),
                ('ASA_ORG_ID', self.org_id),
            ) if not value
        ]
        if missing:
            raise ValueError(f"Missing ASA settings: {', '.join(missing)}")

    # --- Authentication -------------------------------------------------

    def _generate_jwt(self) -> str:
        """Generate the self-signed ES256 JWT used as the OAuth client secret."""
        now = int(time.time())
        payload = {
            'sub': self.client_id,
            'aud': OAUTH_AUDIENCE,
            'iat': now,
            'exp': now + JWT_LIFETIME_SECONDS,
            'iss': self.team_id,
        }
        headers = {'alg': 'ES256', 'kid': self.key_id}
        return jwt.encode(payload, self.private_key, algorithm='ES256', headers=headers)

    def _fetch_access_token(self) -> str:
        """Exchange a fresh JWT for an OAuth access token."""
        data = {
            'grant_type': 'client_credentials',
            'client_id': self.client_id,
            'client_secret': self._generate_jwt(),
            'scope': 'searchadsorg',
        }
        logger.info("AsaClient: requesting OAuth access token")
        try:
            resp = requests.post(OAUTH_TOKEN_URL, data=data, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.Timeout:
            raise AsaTimeoutError("OAuth token request timed out")
        except requests.RequestException as exc:
            raise AsaError(f"OAuth token request failed: {exc}")

        if resp.status_code != 200:
            raise AsaAuthError(
                f"OAuth token exchange failed: {resp.status_code} {resp.text[:300]}"
            )

        token = (resp.json() or {}).get('access_token')
        if not token:
            raise AsaAuthError("OAuth response missing access_token")
        return token

    def _get_access_token(self, force_refresh: bool = False) -> str:
        """Return a cached access token, fetching a new one when needed."""
        if not force_refresh:
            cached = cache.get(ACCESS_TOKEN_CACHE_KEY)
            if cached:
                return cached

        token = self._fetch_access_token()
        cache.set(ACCESS_TOKEN_CACHE_KEY, token, ACCESS_TOKEN_TTL_SECONDS)
        return token

    # --- HTTP -----------------------------------------------------------

    def _make_request(self, method: str, path: str, json_body: dict | None = None) -> dict:
        """Make an authenticated request to the ASA API, retrying once on 401."""
        url = f"{ASA_API_BASE}{path}"

        for attempt in (1, 2):
            token = self._get_access_token(force_refresh=(attempt == 2))
            # X-AP-Context (orgId) is required by the ASA API for accounts
            # with API access. It is NOT sent to the appleid.apple.com
            # OAuth endpoint — only to api.searchads.apple.com.
            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'X-AP-Context': f'orgId={self.org_id}',
            }
            started = time.monotonic()
            try:
                resp = requests.request(
                    method, url, json=json_body, headers=headers,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.Timeout:
                raise AsaTimeoutError(f"{method} {path} timed out")
            except requests.RequestException as exc:
                raise AsaError(f"{method} {path} failed: {exc}")

            elapsed = time.monotonic() - started
            logger.info(
                "AsaClient: %s %s -> %s (%.2fs)", method, path, resp.status_code, elapsed
            )

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    raise AsaServerError(f"Non-JSON 200 response on {path}")

            if resp.status_code == 401:
                cache.delete(ACCESS_TOKEN_CACHE_KEY)
                if attempt == 1:
                    logger.warning("AsaClient: 401 on %s — retrying with fresh token", path)
                    continue
                raise AsaAuthError(f"401 Unauthorized on {path} after token refresh")

            if resp.status_code == 429:
                raise AsaRateLimitError(f"429 Rate limited on {path}")

            if 400 <= resp.status_code < 500:
                raise AsaClientError(f"{resp.status_code} on {path}: {resp.text[:300]}")

            if resp.status_code >= 500:
                raise AsaServerError(f"{resp.status_code} on {path}")

            raise AsaError(f"Unexpected {resp.status_code} on {path}")

        raise AsaError(f"{method} {path} exhausted retries")

    # --- Reports --------------------------------------------------------

    def _report_body(self, start_date, end_date, level, time_zone):
        """Build a DAILY report request body grouped by country."""
        return {
            'startTime': start_date.isoformat(),
            'endTime': end_date.isoformat(),
            'selector': {
                'orderBy': [
                    {'field': _LEVEL_ORDER_FIELD[level], 'sortOrder': 'ASCENDING'}
                ],
                'pagination': {'offset': 0, 'limit': REPORT_PAGE_LIMIT},
            },
            'groupBy': ['countryOrRegion'],
            'timeZone': time_zone,
            'returnRowTotals': False,
            'returnRecordsWithNoMetrics': False,
            'granularity': 'DAILY',
        }

    def _fetch_report(self, path, start_date, end_date, level, time_zone):
        """POST a report request, paging through all result rows."""
        body = self._report_body(start_date, end_date, level, time_zone)
        rows = []
        offset = 0
        while True:
            body['selector']['pagination'] = {'offset': offset, 'limit': REPORT_PAGE_LIMIT}
            data = self._make_request('POST', path, json_body=body)
            page = (
                ((data or {}).get('data') or {})
                .get('reportingDataResponse', {})
                .get('row', [])
            ) or []
            rows.extend(page)
            if len(page) < REPORT_PAGE_LIMIT:
                break
            offset += REPORT_PAGE_LIMIT
        return rows

    def fetch_campaign_report(self, start_date, end_date) -> list[dict]:
        """Fetch the org-wide campaign-level report. POST /reports/campaigns."""
        return self._fetch_report(
            '/reports/campaigns', start_date, end_date, 'campaign', 'UTC'
        )

    def fetch_ad_group_report(self, campaign_id, start_date, end_date) -> list[dict]:
        """Fetch the ad-group-level report for one campaign.
        POST /reports/campaigns/{campaignId}/adgroups."""
        return self._fetch_report(
            f'/reports/campaigns/{campaign_id}/adgroups',
            start_date, end_date, 'ad_group', 'UTC',
        )

    def fetch_keyword_report(self, campaign_id, start_date, end_date) -> list[dict]:
        """Fetch the keyword-level report for one campaign.
        POST /reports/campaigns/{campaignId}/keywords."""
        return self._fetch_report(
            f'/reports/campaigns/{campaign_id}/keywords',
            start_date, end_date, 'keyword', 'UTC',
        )

    def fetch_search_term_report(self, campaign_id, start_date, end_date) -> list[dict]:
        """Fetch the search-term-level report for one campaign.
        POST /reports/campaigns/{campaignId}/searchterms.
        Search Term reports require timeZone ORTZ (org timezone)."""
        return self._fetch_report(
            f'/reports/campaigns/{campaign_id}/searchterms',
            start_date, end_date, 'search_term', 'ORTZ',
        )

    # --- Campaigns ------------------------------------------------------

    def list_campaigns(self) -> list[dict]:
        """List every campaign in the org (active + paused).
        GET /campaigns?limit=1000."""
        campaigns = []
        offset = 0
        while True:
            data = self._make_request('GET', f'/campaigns?limit=1000&offset={offset}')
            page = (data or {}).get('data') or []
            campaigns.extend(page)
            if len(page) < 1000:
                break
            offset += 1000
        return campaigns
