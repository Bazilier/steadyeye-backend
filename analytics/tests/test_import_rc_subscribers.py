"""Tests for the import_rc_subscribers management command."""

import csv
import gzip
import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from analytics.models import RcActiveSubscriber

# Column order of the RC "Active Subscribers" CSV export (semicolon-separated).
HEADER = [
    'project_id', 'project_name', 'app_id', 'app_name', 'first_seen_at',
    'last_seen_at', 'last_seen_app_version', 'last_seen_ip_country',
    'last_seen_platform', 'last_seen_platform_version', 'last_seen_sdk_version',
    'last_seen_locale', 'price_experiment_id', 'price_experiment_variant',
    'email', 'phone_number', 'media_source', 'campaign', 'ad_group', 'ad',
    'keyword', 'creative', 'idfa', 'idfv', 'gps_ad_id', 'custom_attributes',
    'has_made_sandbox_purchase', 'latest_entitlement', 'latest_entitlements',
    'latest_product', 'is_rc_promo', 'first_purchase_at', 'trial_start_at',
    'trial_end_at', 'most_recent_purchase_at', 'most_recent_renewal_at',
    'latest_expiration_at', 'subscription_opt_out_at', 'trial_opt_out_at',
    'total_renewals', 'total_spent', 'latest_store', 'latest_store_country',
    'latest_auto_renew_intent', 'all_purchased_product_ids',
    'most_recent_billing_issues_at', 'status',
    'has_made_a_non_subscription_purchase', 'latest_offer', 'latest_offer_type',
    'latest_purchased_offering', 'latest_ownership_type', 'app_user_id',
    'currency',
]

# A fully-populated subscriber row (locale deliberately contains a ';').
FULL_ROW = {
    'project_id': 'b2ac333d',
    'project_name': 'SteadyEye',
    'app_id': 'app4adaed9546',
    'app_name': 'SteadyEye (App Store)',
    'first_seen_at': '1777707296021',
    'last_seen_at': '1778446300000',
    'last_seen_app_version': '1.3',
    'last_seen_ip_country': 'UZ',
    'last_seen_platform': 'iOS',
    'last_seen_locale': 'en-GB,en;q=0.9',
    'last_seen_sdk_version': '5.66.0',
    'has_made_sandbox_purchase': 'f',
    'latest_entitlement': 'access',
    'latest_product': 'steadyeye_monthly',
    'is_rc_promo': 'false',
    'first_purchase_at': '1777707448000',
    'total_renewals': '0',
    'total_spent': '7.990000000000000',
    'latest_store': 'app_store',
    'latest_store_country': 'UZ',
    'latest_auto_renew_intent': 'f',
    'all_purchased_product_ids': 'steadyeye_annual,steadyeye_monthly',
    'status': 'cancelled',
    'has_made_a_non_subscription_purchase': 'f',
    'latest_offer_type': 'no_offer',
    'latest_purchased_offering': 'default',
    'latest_ownership_type': 'PURCHASED',
    'app_user_id': '$RCAnonymousID:6f59803d086949c6ba8cca8b966ef90a',
    'currency': 'USD',
}


def _write_csv(path: Path, rows: list[dict], gz: bool = False):
    """Write rows to a semicolon-separated CSV (optionally gzipped)."""
    opener = gzip.open if gz else open
    with opener(path, mode='wt', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter=';')
        writer.writerow(HEADER)
        for row in rows:
            writer.writerow([row.get(col, '') for col in HEADER])


class ImportRcSubscribersTests(TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def _path(self, name: str) -> Path:
        return self.tmpdir / name

    def test_parses_sample_csv(self):
        empty_row = {'app_user_id': '$RCAnonymousID:second', 'last_seen_ip_country': 'MX'}
        path = self._path('subs.csv')
        _write_csv(path, [FULL_ROW, empty_row])

        call_command('import_rc_subscribers', str(path))

        self.assertEqual(RcActiveSubscriber.objects.count(), 2)
        sub = RcActiveSubscriber.objects.get(pk=FULL_ROW['app_user_id'])
        self.assertEqual(sub.last_seen_ip_country, 'UZ')
        self.assertEqual(sub.latest_product, 'steadyeye_monthly')
        self.assertEqual(sub.status, 'cancelled')
        self.assertEqual(sub.total_spent, Decimal('7.99'))
        self.assertEqual(sub.last_seen_locale, 'en-GB,en;q=0.9')  # ';' inside quoted field
        self.assertIsNotNone(sub.first_purchase_at)
        self.assertEqual(sub.first_purchase_at.year, 2026)
        self.assertFalse(sub.is_rc_promo)
        self.assertFalse(sub.latest_auto_renew_intent)
        self.assertEqual(sub.source_file, 'subs.csv')

    def test_handles_empty_fields(self):
        path = self._path('sparse.csv')
        _write_csv(path, [{'app_user_id': '$RCAnonymousID:sparse'}])

        call_command('import_rc_subscribers', str(path))

        sub = RcActiveSubscriber.objects.get(pk='$RCAnonymousID:sparse')
        self.assertEqual(sub.total_spent, Decimal('0'))
        self.assertEqual(sub.total_renewals, 0)
        self.assertFalse(sub.is_rc_promo)
        self.assertIsNone(sub.first_seen_at)
        self.assertIsNone(sub.latest_auto_renew_intent)
        self.assertEqual(sub.status, '')
        self.assertEqual(sub.email, '')

    def test_handles_gzipped_file(self):
        path = self._path('subs.csv.gz')
        _write_csv(path, [FULL_ROW], gz=True)

        call_command('import_rc_subscribers', str(path))

        self.assertEqual(RcActiveSubscriber.objects.count(), 1)
        self.assertEqual(
            RcActiveSubscriber.objects.get().source_file, 'subs.csv.gz'
        )

    def test_idempotent(self):
        path = self._path('subs.csv')
        _write_csv(path, [FULL_ROW])

        first = StringIO()
        call_command('import_rc_subscribers', str(path), stdout=first)
        self.assertIn('created=1', first.getvalue())

        second = StringIO()
        call_command('import_rc_subscribers', str(path), stdout=second)
        self.assertIn('created=0', second.getvalue())
        self.assertIn('updated=1', second.getvalue())

        self.assertEqual(RcActiveSubscriber.objects.count(), 1)

    def test_dry_run_writes_nothing(self):
        path = self._path('subs.csv')
        _write_csv(path, [FULL_ROW])

        out = StringIO()
        call_command('import_rc_subscribers', str(path), '--dry-run', stdout=out)

        self.assertEqual(RcActiveSubscriber.objects.count(), 0)
        self.assertIn('dry-run', out.getvalue())

    def test_missing_app_user_id_is_skipped(self):
        path = self._path('bad.csv')
        _write_csv(path, [{'app_user_id': '', 'last_seen_ip_country': 'US'}])

        out = StringIO()
        # Must not raise — the bad row is counted as an error and skipped.
        call_command('import_rc_subscribers', str(path), stdout=out)

        self.assertEqual(RcActiveSubscriber.objects.count(), 0)
        self.assertIn('1 errors', out.getvalue())
