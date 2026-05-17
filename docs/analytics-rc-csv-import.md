# Analytics — RevenueCat CSV import (Phase 2.5)

The webhook (`/api/v1/analytics/webhooks/revenuecat/`) only captures RC
events that happen **after** it was configured. Subscribers who purchased
earlier are missing from the database. To backfill them, RevenueCat's
dashboard provides an "Active Subscribers" CSV export, which the
`import_rc_subscribers` command loads into the `RcActiveSubscriber` table.

## Exporting the CSV from RevenueCat

1. RC dashboard → **Customer Lists** (or **Customers**).
2. Open / create the **Active Subscribers** list.
3. Use **Export** → download the CSV. RC delivers a semicolon-separated
   file (~54 columns), often gzipped (`.csv.gz`).

## Importing

```bash
# Gzipped export (suffix .gz is auto-detected)
python manage.py import_rc_subscribers /path/to/Active_Subscribers.csv.gz

# Plain CSV
python manage.py import_rc_subscribers /path/to/file.csv

# Parse + validate only, write nothing
python manage.py import_rc_subscribers /path/to/file.csv --dry-run
```

The command prints a summary:
`import_rc_subscribers complete: N rows, imported (created=X, updated=Y), E errors`.

Getting the file onto Railway (no shared filesystem): base64-encode it
locally and decode it inside a Railway shell, e.g.

```bash
# local
base64 -i Active_Subscribers.csv.gz | pbcopy
# Railway shell
pbpaste-equivalent > /tmp/subs.b64   # paste the clipboard contents
base64 -d /tmp/subs.b64 > /tmp/subs.csv.gz
python manage.py import_rc_subscribers /tmp/subs.csv.gz
```

## Behaviour notes

- **Idempotent.** The row key is `app_user_id`; re-importing the same file
  (or a newer export) updates existing rows via `update_or_create` —
  re-runs never duplicate data.
- **Robust parsing.** Unix-millis timestamps become aware datetimes;
  `t`/`f` becomes booleans; blank numeric fields default to `0`; over-long
  string values are truncated to each field's `max_length`. A row with no
  `app_user_id` is counted as an error and skipped — it never aborts the
  import.
- **Snapshot, not a log.** `RcActiveSubscriber` is a point-in-time snapshot
  of subscriber state at export time. It complements — does not replace —
  the live `RcEvent` stream from the webhook:
  - `RcActiveSubscriber` — current state per subscriber (one row each).
  - `RcEvent` — append-only event history (purchases, renewals, etc.).
  Joining the two (e.g. attributing revenue to campaigns) is left to
  Metabase SQL in Phase 4.
- **Privacy.** The table stores `email` / `phone_number` for support
  purposes. The data is private; access is restricted to the founder.
