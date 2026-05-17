import csv
import gzip
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from analytics.models import RcActiveSubscriber


class Command(BaseCommand):
    help = "Import active subscribers from RC dashboard CSV export."

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str, help='Path to the CSV file (can be .csv or .csv.gz)')
        parser.add_argument('--dry-run', action='store_true', help='Parse and validate but do not write to DB')

    def handle(self, *args, **options):
        csv_path = Path(options['csv_path'])
        dry_run = options['dry_run']

        if not csv_path.exists():
            raise CommandError(f"File not found: {csv_path}")

        # Open file (handle .gz)
        opener = gzip.open if csv_path.suffix == '.gz' else open

        with opener(csv_path, mode='rt', encoding='utf-8') as f:
            reader = csv.DictReader(f, delimiter=';')

            stats = {'total': 0, 'created': 0, 'updated': 0, 'errors': 0}

            for row in reader:
                stats['total'] += 1
                try:
                    data = self._parse_row(row, source_file=csv_path.name)

                    if dry_run:
                        self.stdout.write(
                            f"  [dry-run] would upsert {data['app_user_id']} "
                            f"({data['last_seen_ip_country']} {data['latest_product']})"
                        )
                        continue

                    with transaction.atomic():
                        obj, created = RcActiveSubscriber.objects.update_or_create(
                            app_user_id=data['app_user_id'],
                            defaults={k: v for k, v in data.items() if k != 'app_user_id'},
                        )
                        if created:
                            stats['created'] += 1
                        else:
                            stats['updated'] += 1

                except Exception as e:
                    stats['errors'] += 1
                    self.stdout.write(self.style.WARNING(f"  Row error: {e}"))

        # Summary
        verb = "would import" if dry_run else f"imported (created={stats['created']}, updated={stats['updated']})"
        self.stdout.write(self.style.SUCCESS(
            f"import_rc_subscribers complete: {stats['total']} rows, {verb}, {stats['errors']} errors"
        ))

    def _parse_row(self, row: dict, source_file: str) -> dict:
        """Parse one CSV row into model fields."""

        def s(key, default=''):
            """Safe string getter, strips quotes and whitespace."""
            val = row.get(key, default)
            return val.strip() if val else default

        def ts(key):
            """Parse Unix millis timestamp into aware datetime."""
            val = s(key)
            if not val:
                return None
            try:
                return datetime.fromtimestamp(int(val) / 1000, tz=timezone.utc)
            except (ValueError, OverflowError):
                return None

        def b(key):
            """Parse RC boolean ('t'/'f'/'true'/'false') into bool, blank → None."""
            val = s(key).lower()
            if val in ('t', 'true', '1', 'yes'):
                return True
            if val in ('f', 'false', '0', 'no'):
                return False
            return None

        def dec(key):
            """Parse decimal, blank → 0."""
            val = s(key)
            if not val:
                return Decimal('0')
            try:
                return Decimal(val)
            except InvalidOperation:
                return Decimal('0')

        def i(key):
            """Parse int, blank → 0."""
            val = s(key)
            if not val:
                return 0
            try:
                return int(val)
            except ValueError:
                return 0

        app_user_id = s('app_user_id')
        if not app_user_id:
            raise ValueError("Missing app_user_id")

        return {
            'app_user_id': app_user_id,
            'project_id': s('project_id'),
            'project_name': s('project_name'),
            'app_id': s('app_id'),
            'app_name': s('app_name'),
            'first_seen_at': ts('first_seen_at'),
            'last_seen_at': ts('last_seen_at'),
            'first_purchase_at': ts('first_purchase_at'),
            'trial_start_at': ts('trial_start_at'),
            'trial_end_at': ts('trial_end_at'),
            'most_recent_purchase_at': ts('most_recent_purchase_at'),
            'most_recent_renewal_at': ts('most_recent_renewal_at'),
            'latest_expiration_at': ts('latest_expiration_at'),
            'subscription_opt_out_at': ts('subscription_opt_out_at'),
            'trial_opt_out_at': ts('trial_opt_out_at'),
            'most_recent_billing_issues_at': ts('most_recent_billing_issues_at'),
            'last_seen_app_version': s('last_seen_app_version')[:20],
            'last_seen_ip_country': s('last_seen_ip_country')[:2],
            'last_seen_platform': s('last_seen_platform')[:20],
            'last_seen_platform_version': s('last_seen_platform_version')[:200],
            'last_seen_sdk_version': s('last_seen_sdk_version')[:20],
            'last_seen_locale': s('last_seen_locale')[:100],
            'media_source': s('media_source')[:100],
            'campaign': s('campaign')[:200],
            'ad_group': s('ad_group')[:200],
            'ad': s('ad')[:200],
            'keyword': s('keyword')[:200],
            'creative': s('creative')[:200],
            'latest_entitlement': s('latest_entitlement')[:50],
            'latest_entitlements': s('latest_entitlements')[:200],
            'latest_product': s('latest_product')[:100],
            'all_purchased_product_ids': s('all_purchased_product_ids'),
            'is_rc_promo': b('is_rc_promo') or False,
            'status': s('status')[:30],
            'total_renewals': i('total_renewals'),
            'total_spent': dec('total_spent'),
            'currency': s('currency')[:3],
            'latest_store': s('latest_store')[:30],
            'latest_store_country': s('latest_store_country')[:2],
            'latest_auto_renew_intent': b('latest_auto_renew_intent'),
            'latest_offer': s('latest_offer')[:200],
            'latest_offer_type': s('latest_offer_type')[:50],
            'latest_purchased_offering': s('latest_purchased_offering')[:100],
            'latest_ownership_type': s('latest_ownership_type')[:30],
            'has_made_sandbox_purchase': b('has_made_sandbox_purchase') or False,
            'has_made_a_non_subscription_purchase': b('has_made_a_non_subscription_purchase') or False,
            'email': s('email')[:254] if '@' in s('email') else '',
            'phone_number': s('phone_number')[:30],
            'price_experiment_id': s('price_experiment_id')[:50],
            'price_experiment_variant': s('price_experiment_variant')[:50],
            'custom_attributes': s('custom_attributes'),
            'source_file': source_file[:200],
        }
