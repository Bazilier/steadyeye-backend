"""Pull RevenueCat subscriber snapshots into RcSubscriptionSnapshot.

The RC v1 REST secret key has no "list all subscribers" endpoint, so the
set of app_user_ids is either passed explicitly via --app-user-ids or
derived from the RcEvent table (populated by the webhook).
"""

import datetime as dt
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from analytics.alerts import send_alert
from analytics.models import RcEvent, RcSubscriptionSnapshot
from analytics.services.rc_client import RcClient, RcClientError

# Rough monthly-recurring-revenue contribution per product type. The RC v1
# REST API does not reliably expose a per-user price, so Phase 2 uses these
# hardcoded list prices (USD).
PRODUCT_PRICES = {
    'annual': 49.99 / 12,   # ~4.17/mo
    'monthly': 6.99,
    'lifetime': 0,          # one-time purchase, not recurring
}


def _product_type(product_id: str | None) -> str | None:
    """Map an RC product identifier to a coarse product type by substring."""
    if not product_id:
        return None
    pid = product_id.lower()
    if 'annual' in pid:
        return 'annual'
    if 'monthly' in pid:
        return 'monthly'
    if 'lifetime' in pid:
        return 'lifetime'
    return None


def _parse_dt(value):
    """Parse an RC ISO-8601 timestamp into a tz-aware datetime, or None."""
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt.timezone.utc)
    return parsed


def _attr_value(attributes: dict, key: str):
    """RC stores each subscriber attribute as {value, updated_at_ms}."""
    obj = attributes.get(key)
    if isinstance(obj, dict):
        val = obj.get('value')
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


class Command(BaseCommand):
    help = "Pull RC subscriber snapshots for the given date (today by default)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='YYYY-MM-DD format, defaults to today UTC',
        )
        parser.add_argument(
            '--app-user-ids',
            type=str,
            help='Comma-separated list of specific app_user_ids to pull. If omitted, '
                 'pulls all known app_user_ids from RcEvent table.',
        )

    def handle(self, *args, **options):
        # 1. Parse target date (default: today UTC).
        date_arg = options.get('date')
        if date_arg:
            try:
                target_date = dt.date.fromisoformat(date_arg)
            except ValueError:
                raise CommandError(f"--date must be YYYY-MM-DD, got: {date_arg!r}")
        else:
            target_date = timezone.now().date()

        # 2. Resolve the list of app_user_ids.
        ids_arg = options.get('app_user_ids')
        if ids_arg:
            app_user_ids = [uid.strip() for uid in ids_arg.split(',') if uid.strip()]
        else:
            app_user_ids = list(
                RcEvent.objects.values_list('app_user_id', flat=True)
                .distinct()
                .order_by('app_user_id')
            )
            app_user_ids = [uid for uid in app_user_ids if uid]

        self.stdout.write(
            f"pull_rc: {len(app_user_ids)} app_user_id(s) for {target_date.isoformat()}"
        )

        if not app_user_ids:
            self.stdout.write(
                self.style.SUCCESS("pull_rc complete: 0 fetched, 0 skipped, 0 errors")
            )
            return

        try:
            client = RcClient()
        except ValueError as exc:
            raise CommandError(str(exc))

        fetched = 0
        skipped = 0
        errors = 0

        # 3-5. Fetch + upsert each subscriber, tracking stats.
        for app_user_id in app_user_ids:
            try:
                subscriber = client.fetch_subscriber(app_user_id)
            except RcClientError as exc:
                errors += 1
                self.stderr.write(
                    self.style.ERROR(f"  {app_user_id}: RC error ({exc})")
                )
                continue

            if subscriber is None:
                # 404 — subscriber doesn't exist on RC yet. Skip silently.
                skipped += 1
                continue

            try:
                self._upsert_snapshot(target_date, app_user_id, subscriber)
            except Exception as exc:  # noqa: BLE001 — keep the batch going
                errors += 1
                self.stderr.write(
                    self.style.ERROR(f"  {app_user_id}: upsert failed ({exc})")
                )
                continue

            fetched += 1

        # 6. Summary + alert.
        summary = f"pull_rc complete: {fetched} fetched, {skipped} skipped, {errors} errors"
        self.stdout.write(self.style.SUCCESS(summary))
        if errors > 0:
            send_alert(f"pull_rc had {errors} errors", severity="warning")

    def _upsert_snapshot(self, target_date, app_user_id, subscriber):
        """Parse one RC subscriber payload and upsert its daily snapshot."""
        now = timezone.now()

        entitlements = subscriber.get('entitlements') or {}
        subscriptions = subscriber.get('subscriptions') or {}
        attributes = subscriber.get('subscriber_attributes') or {}

        access = entitlements.get('access')
        product_id = None
        period_type = ''
        subscription_status = 'none'

        if access:
            product_id = access.get('product_identifier')
            expires = _parse_dt(access.get('expires_date'))
            sub = subscriptions.get(product_id, {}) if product_id else {}
            period_type = (sub.get('period_type') or '').lower()

            if expires is not None and expires <= now:
                subscription_status = 'expired'
            elif period_type == 'trial':
                subscription_status = 'trial'
            else:
                # Either a non-expiring entitlement (lifetime) or an active
                # paid/intro subscription.
                subscription_status = 'active'
        else:
            sub = {}

        # Disc 50% offer detection — best effort: RC exposes redeemed offer
        # codes on the subscription object when present.
        is_discounted = bool(sub.get('offer_codes'))

        country = _attr_value(attributes, '$country')
        if country:
            country = country[:2].upper()
        media_source = _attr_value(attributes, '$mediaSource')

        # MRR contribution.
        mrr = 0.0
        if subscription_status == 'active':
            mrr = PRODUCT_PRICES.get(_product_type(product_id), 0)
            if is_discounted:
                mrr *= 0.5

        snapshot_user_id = subscriber.get('original_app_user_id') or app_user_id

        self.stdout.write(
            f"  {app_user_id}: status={subscription_status} product={product_id} "
            f"country={country} media_source={media_source} mrr={mrr:.2f}"
        )

        with transaction.atomic():
            RcSubscriptionSnapshot.objects.update_or_create(
                snapshot_date=target_date,
                app_user_id=snapshot_user_id,
                defaults={
                    'country': country,
                    'product_id': product_id,
                    'subscription_status': subscription_status,
                    'mrr_usd': Decimal(str(round(mrr, 2))),
                    'is_discounted': is_discounted,
                },
            )
