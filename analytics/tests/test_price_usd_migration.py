"""Tests for migration 0005: price_usd recomputed from RC's USD `price`."""

import importlib
from datetime import datetime, timezone
from decimal import Decimal

from django.apps import apps
from django.test import TestCase

from analytics.models import RcEvent

migration = importlib.import_module('analytics.migrations.0005_rcevent_price_usd_from_price')


class RecomputePriceUsdTests(TestCase):

    def _row(self, event_id, price_usd, currency, event):
        return RcEvent.objects.create(
            event_id=event_id,
            event_type='INITIAL_PURCHASE',
            occurred_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
            app_user_id='u',
            price_usd=price_usd,
            currency=currency,
            raw_payload={'event': event},
        )

    def test_local_amount_replaced_by_usd_price(self):
        self._row('brl', Decimal('19.90'), 'BRL', {'price': 3.993, 'price_in_purchased_currency': 19.9})
        migration.recompute_price_usd(apps, None)
        self.assertEqual(RcEvent.objects.get(event_id='brl').price_usd, Decimal('3.99'))

    def test_missing_price_keeps_usd_and_clears_foreign(self):
        self._row('usd', Decimal('6.99'), 'USD', {})
        self._row('cop', Decimal('49900.00'), 'COP', {})
        migration.recompute_price_usd(apps, None)
        self.assertEqual(RcEvent.objects.get(event_id='usd').price_usd, Decimal('6.99'))
        self.assertIsNone(RcEvent.objects.get(event_id='cop').price_usd)
