"""Tests for the pull_asa command — date-range resolution, idempotent
upserts, and graceful handling of ASA API errors.

AsaClient is replaced with a MagicMock so no network calls are made.
"""

import datetime as dt
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from analytics.management.commands.pull_asa import resolve_date_range
from analytics.models import AsaDailyMetrics
from analytics.services.asa_client import AsaError

TODAY = dt.date(2026, 5, 17)


class DateRangeResolutionTests(TestCase):

    def test_start_and_end(self):
        self.assertEqual(
            resolve_date_range({'start': '2026-05-01', 'end': '2026-05-10'}, TODAY),
            (dt.date(2026, 5, 1), dt.date(2026, 5, 10)),
        )

    def test_backfill_since(self):
        self.assertEqual(
            resolve_date_range({'backfill_since': '2026-05-01'}, TODAY),
            (dt.date(2026, 5, 1), TODAY),
        )

    def test_single_date(self):
        self.assertEqual(
            resolve_date_range({'date': '2026-05-15'}, TODAY),
            (dt.date(2026, 5, 15), dt.date(2026, 5, 15)),
        )

    def test_days_back(self):
        self.assertEqual(
            resolve_date_range({'days': 3}, TODAY),
            (dt.date(2026, 5, 15), TODAY),
        )

    def test_default_is_today(self):
        self.assertEqual(resolve_date_range({}, TODAY), (TODAY, TODAY))

    def test_priority_start_end_over_date(self):
        result = resolve_date_range(
            {'start': '2026-05-01', 'end': '2026-05-02', 'date': '2026-05-15'}, TODAY
        )
        self.assertEqual(result, (dt.date(2026, 5, 1), dt.date(2026, 5, 2)))


@override_settings(
    ASA_PRIVATE_KEY='dummy', ASA_CLIENT_ID='dummy',
    ASA_TEAM_ID='dummy', ASA_KEY_ID='dummy',
    TELEGRAM_BOT_TOKEN='', TELEGRAM_OWNER_CHAT_ID='',
)
class PullAsaCommandTests(TestCase):

    def _campaign_report(self):
        return [
            {
                'metadata': {'campaignId': 1, 'campaignName': 'Brand US',
                             'countryOrRegion': 'US'},
                'granularity': [
                    {
                        'date': '2026-05-15',
                        'impressions': 100,
                        'taps': 10,
                        'tapInstalls': 3,
                        'viewInstalls': 1,
                        'localSpend': {'amount': '12.50', 'currency': 'USD'},
                        'avgCPT': {'amount': '1.25', 'currency': 'USD'},
                    },
                ],
            },
        ]

    def _fake_client(self):
        client = MagicMock()
        client.fetch_campaign_report.return_value = self._campaign_report()
        client.list_campaigns.return_value = []
        return client

    def test_idempotent_upsert(self):
        fake = self._fake_client()
        with patch('analytics.management.commands.pull_asa.AsaClient', return_value=fake):
            call_command('pull_asa', '--date', '2026-05-15', '--levels', 'campaign')
            call_command('pull_asa', '--date', '2026-05-15', '--levels', 'campaign')

        self.assertEqual(AsaDailyMetrics.objects.count(), 1)
        row = AsaDailyMetrics.objects.get()
        self.assertEqual(row.level, 'campaign')
        self.assertEqual(row.campaign_name, 'Brand US')
        self.assertEqual(row.country, 'US')
        self.assertEqual(row.impressions, 100)
        self.assertEqual(row.installs, 4)                 # 3 tap + 1 view
        self.assertEqual(row.installs_tap_through, 3)
        self.assertEqual(row.installs_view_through, 1)
        self.assertEqual(str(row.spend), '12.50')

    def test_error_handling_does_not_crash(self):
        fake = MagicMock()
        fake.fetch_campaign_report.side_effect = AsaError("upstream boom")
        fake.list_campaigns.return_value = []
        with patch('analytics.management.commands.pull_asa.AsaClient', return_value=fake):
            # Must not raise — the report error is caught and counted.
            call_command('pull_asa', '--date', '2026-05-15', '--levels', 'campaign')

        self.assertEqual(AsaDailyMetrics.objects.count(), 0)

    def test_ttr_derived_as_ratio(self):
        fake = self._fake_client()
        with patch('analytics.management.commands.pull_asa.AsaClient', return_value=fake):
            call_command('pull_asa', '--date', '2026-05-15', '--levels', 'campaign')

        row = AsaDailyMetrics.objects.get()
        self.assertEqual(str(row.ttr), '0.1000')          # 10 taps / 100 impressions
