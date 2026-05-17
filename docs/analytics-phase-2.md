# Analytics — Phase 2 (RevenueCat)

Phase 2 adds the RevenueCat side of the analytics system: a REST client, a
`pull_rc` snapshot command, and a webhook endpoint that ingests RC events
and fires Telegram alerts.

## Running `pull_rc` manually

`pull_rc` fetches subscriber state from the RC REST API and upserts one
`RcSubscriptionSnapshot` row per `app_user_id` for a given date.

```bash
# Snapshot every app_user_id seen in the RcEvent table, for today (UTC)
python manage.py pull_rc

# Snapshot for a specific date
python manage.py pull_rc --date 2026-05-17

# Snapshot specific users only (comma-separated)
python manage.py pull_rc --app-user-ids "uuid-1,uuid-2"
```

Behaviour:

- Without `--app-user-ids`, the user list is the `DISTINCT app_user_id`
  values already stored in `RcEvent` (populated by the webhook). On a fresh
  install this is empty, so the command prints `0 fetched, 0 skipped, 0
  errors` and exits cleanly.
- A `404` from RC (subscriber not yet known to RC) is **skipped silently** —
  no snapshot row is written.
- Other RC errors are logged, counted, and the batch continues.
- The command ends with `pull_rc complete: {fetched} fetched, {skipped}
  skipped, {errors} errors`. If `errors > 0`, a `warning` Telegram alert is
  sent.

The upsert is idempotent: re-running for the same date overwrites the row
(`update_or_create` on `snapshot_date` + `app_user_id`).

## Setting up the RC webhook

In the RevenueCat dashboard: **Project settings → Integrations → Webhooks**.

- **URL:** `https://web-production-49244.up.railway.app/api/v1/analytics/webhooks/revenuecat/`
- **Authorization header:** `Bearer <REVENUECAT_WEBHOOK_SECRET>` — the value
  must match the `REVENUECAT_WEBHOOK_SECRET` env var set in Railway.

The endpoint:

- Returns `401` if the `Authorization` header is missing or wrong.
- Returns `500 {"error": "misconfigured"}` if `REVENUECAT_WEBHOOK_SECRET` is
  not set server-side.
- Returns `400` if the body is malformed or `event.id` / `event.type` is
  missing.
- Stores the event idempotently in `RcEvent` (keyed on RC's `event.id`), so
  RC's retries never create duplicate rows.
- Responds `200` quickly; RC retries on timeout.

## Event types & alerts

Every event RC sends is stored in `RcEvent` (full body in `raw_payload`).
Telegram alerts fire only for these types:

| Event type        | Telegram alert | Severity  |
| ----------------- | -------------- | --------- |
| `INITIAL_PURCHASE`| 💰 New paying customer | info     |
| `CANCELLATION`    | ❌ Cancellation        | warning  |
| `BILLING_ISSUE`   | ⚠️ Billing issue       | warning  |
| `RENEWAL`         | — (none, too noisy)    | —         |
| all other types   | — (stored only)        | —         |

`app_user_id` is truncated to its first 8 characters in alert text for
privacy.

## Known limitations

- **No batch subscriber list.** The RC v1 REST secret key has no
  "list all subscribers" endpoint. `RcClient.list_active_subscribers_from_overview()`
  is a placeholder returning `[]`. The set of users to snapshot is therefore
  bootstrapped from webhook events (`RcEvent`); historical subscribers from
  before the webhook was wired up will not appear until they generate an
  event, or until their `app_user_id` is passed explicitly via
  `--app-user-ids`.
- **MRR is approximate.** The RC v1 REST API does not reliably expose a
  per-user price, so `pull_rc` uses hardcoded list prices
  (annual ≈ $4.17/mo, monthly $6.99, lifetime $0). Discounted ("Disc 50%")
  subscriptions are detected best-effort via the subscription's
  `offer_codes` and halve the MRR contribution.
- **`revenue_today_usd` is not computed** by `pull_rc` — it stays at its
  default `0`. Daily revenue is better derived from `RcEvent` and is left
  to a later phase.
- **No automated scheduling.** `pull_rc` is run manually for now; no cron
  job exists in any phase yet.
