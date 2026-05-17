"""Tests for the RevenueCat webhook endpoint.

Telegram is disabled via empty TELEGRAM_* settings so `send_alert` is a
silent no-op — no network calls leave the test process.
"""

import json

from django.test import TestCase, override_settings

from analytics.models import RcEvent

WEBHOOK_URL = '/api/v1/analytics/webhooks/revenuecat/'
TEST_SECRET = 'test-webhook-secret'


@override_settings(
    REVENUECAT_WEBHOOK_SECRET=TEST_SECRET,
    TELEGRAM_BOT_TOKEN='',
    TELEGRAM_OWNER_CHAT_ID='',
)
class RevenueCatWebhookTests(TestCase):

    def _event_payload(self, **overrides):
        event = {
            'id': 'evt_initial_001',
            'type': 'INITIAL_PURCHASE',
            'event_timestamp_ms': 1715900000000,
            'app_user_id': 'user-abc-12345',
            'product_id': 'com.bazilier.steadyeye.annual',
            'price_in_purchased_currency': 49.99,
            'currency': 'USD',
            'country_code': 'US',
            'is_trial_conversion': False,
            'period_type': 'NORMAL',
        }
        event.update(overrides)
        return {'event': event, 'api_version': '1.0'}

    def _post(self, payload, auth=f'Bearer {TEST_SECRET}'):
        headers = {'Authorization': auth} if auth is not None else {}
        return self.client.post(
            WEBHOOK_URL,
            data=json.dumps(payload),
            content_type='application/json',
            headers=headers,
        )

    def test_rejects_without_auth_header(self):
        resp = self._post(self._event_payload(), auth=None)
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(RcEvent.objects.count(), 0)

    def test_rejects_wrong_token(self):
        resp = self._post(self._event_payload(), auth='Bearer wrong-token')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(RcEvent.objects.count(), 0)

    def test_accepts_valid_initial_purchase(self):
        resp = self._post(self._event_payload())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'ok')

        self.assertEqual(RcEvent.objects.count(), 1)
        event = RcEvent.objects.get()
        self.assertEqual(event.event_id, 'evt_initial_001')
        self.assertEqual(event.event_type, 'INITIAL_PURCHASE')
        self.assertEqual(event.app_user_id, 'user-abc-12345')
        self.assertEqual(event.product_id, 'com.bazilier.steadyeye.annual')
        self.assertEqual(event.country, 'US')
        self.assertFalse(event.is_renewal)
        self.assertEqual(event.raw_payload['api_version'], '1.0')

    def test_idempotency_same_event_id_creates_one_row(self):
        first = self._post(self._event_payload())
        second = self._post(self._event_payload())
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(RcEvent.objects.count(), 1)

    def test_renewal_event_sets_is_renewal_flag(self):
        resp = self._post(self._event_payload(id='evt_renewal_001', type='RENEWAL'))
        self.assertEqual(resp.status_code, 200)
        event = RcEvent.objects.get(event_id='evt_renewal_001')
        self.assertTrue(event.is_renewal)

    def test_malformed_body_missing_event_fields(self):
        resp = self._post({'event': {}})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(RcEvent.objects.count(), 0)

    def test_malformed_json_body(self):
        resp = self.client.post(
            WEBHOOK_URL,
            data='{not valid json',
            content_type='application/json',
            headers={'Authorization': f'Bearer {TEST_SECRET}'},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(RcEvent.objects.count(), 0)
