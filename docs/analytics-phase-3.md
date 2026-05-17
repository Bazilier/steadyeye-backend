# Analytics — Phase 3 (Apple Search Ads)

Phase 3 adds the Apple Search Ads side: a JWT-authenticated client for the
Apple Ads Campaign Management API v5 and a `pull_asa` command that snapshots
daily metrics into `AsaDailyMetrics`.

## Running `pull_asa`

`pull_asa` resolves a date range, lists every campaign in the org, fetches
reports at the requested levels, and upserts one `AsaDailyMetrics` row per
day × level × campaign × country × search_term × keyword × match_type.

```bash
# A single day, default levels (campaign + search_term)
python manage.py pull_asa --date 2026-05-15

# Last 7 days ending today
python manage.py pull_asa --days 7

# An explicit range
python manage.py pull_asa --start 2026-05-01 --end 2026-05-15

# Backfill from a date up to today
python manage.py pull_asa --backfill-since 2026-04-01

# Pick which report levels to pull
python manage.py pull_asa --date 2026-05-15 --levels campaign,keyword,search_term
```

### Argument priority for the date range

1. `--start` **and** `--end` → that exact range (both required together).
2. `--backfill-since` → from that date through today.
3. `--date` → that single day.
4. `--days N` → the N days ending today (`--days 1`, the default, = today only).

### Levels

`--levels` accepts a comma-separated subset of
`campaign,ad_group,keyword,search_term`; the default is
`campaign,search_term`.

- The **campaign** report is one org-wide API call.
- **ad_group / keyword / search_term** reports are fetched per campaign.

At the end the command prints `pull_asa complete: N rows upserted (M new),
E errors` with a per-level breakdown, sends a `warning` Telegram alert if
any report failed, and an `info` alert if new rows were inserted.

## How JWT auth works

The Apple Ads API uses OAuth where the client secret is a self-signed JWT:

1. Build a JWT — header `{alg: ES256, kid: ASA_KEY_ID}`, claims
   `sub=ASA_CLIENT_ID`, `iss=ASA_TEAM_ID`, `aud=https://appleid.apple.com`,
   `iat`/`exp` (60 min) — signed with the EC P-256 `ASA_PRIVATE_KEY` (PEM).
2. POST it to `https://appleid.apple.com/auth/oauth2/token` with
   `grant_type=client_credentials` and `scope=searchadsorg` to get an
   access token.
3. The access token is cached for 55 minutes and sent as a bearer token to
   `https://api.searchads.apple.com/api/v5`.
4. On a `401`, the cached token is dropped, a fresh one minted, and the
   request retried once; a second `401` raises `AsaAuthError`.

The private key and JWT are never logged.

### The X-AP-Context header

The Apple Ads API requires an `X-AP-Context` header naming the org on
**every** call to `api.searchads.apple.com`:

```
X-AP-Context: orgId=<ASA_ORG_ID>
```

Without it the API returns `403 FORBIDDEN — "A required header was not
specified or was invalid"`. `AsaClient` adds this header to all API
requests but **not** to the OAuth token exchange at `appleid.apple.com`.
The org ID is the numeric account ID shown in the Apple Ads dashboard URL.

Required env vars (set in Railway): `ASA_PRIVATE_KEY` (full multiline PEM),
`ASA_CLIENT_ID`, `ASA_TEAM_ID`, `ASA_KEY_ID`, `ASA_ORG_ID` (numeric org ID).

## Known limitations

- **Reporting lag.** ASA data lags ~3–6 hours; reports for the last 24–48h
  may be empty or partial. Empty results for very recent days are normal,
  not an error.
- **No `ad_group` column in the model.** `AsaDailyMetrics` has no ad-group
  field. For `--levels ad_group`, the ad group name is stored in the
  `keyword` column so rows stay distinct — a pragmatic workaround. If
  ad-group reporting becomes important, add a dedicated `ad_group_name`
  field and extend the unique key.
- **`ttr` is derived.** The model's `ttr` is `Decimal(5,4)` (max 9.9999),
  too small for ASA's percentage-style `ttr`. `pull_asa` instead stores
  `taps / impressions` as a 0–1 ratio.
- **Search Term reports use `timeZone: ORTZ`** (org timezone), as Apple
  requires; all other report types use `UTC`.
- **"Low volume terms"** placeholder search terms (<5 impressions) are
  stored verbatim as returned by Apple.
- **`orderBy` fields** in report selectors are best-effort per level
  (`campaignId` / `adGroupId` / `keywordId`); adjust if Apple rejects them
  for a given org.
- **`X-AP-Context: orgId=<ASA_ORG_ID>`** is sent on every API call (see
  above); the request fails with `403` if `ASA_ORG_ID` is unset or wrong.

## Verifying data correctness

Cross-check a pulled day against the Apple Search Ads UI:

1. Run `pull_asa --date <YYYY-MM-DD>`.
2. In the Django shell, sum a campaign's metrics for that date:
   ```python
   from analytics.models import AsaDailyMetrics
   from django.db.models import Sum
   AsaDailyMetrics.objects.filter(date='2026-05-15', level='campaign').aggregate(
       Sum('spend'), Sum('impressions'), Sum('taps'), Sum('installs'))
   ```
3. Compare spend / impressions / taps / installs against the same date in
   the ASA dashboard. Small differences can come from timezone (`UTC` vs
   `ORTZ`) and the reporting lag.
