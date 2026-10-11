"""Tests for the iPhone Duo diagnostic report endpoint.

`requests.post` in analytics.duo_telegram is patched in every test, so no
network call leaves the test process.
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import requests
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from analytics.models import DuoReport

URL = '/api/v1/analytics/duo-report/'
INSTALL_ID = '3F2504E0-4F89-11D3-9A0C-0305E82C3301'


def _ok_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.text = '{"ok":true}'
    return resp


@override_settings(
    TELEGRAM_BOT_TOKEN='test-bot-token',
    TELEGRAM_OWNER_CHAT_ID='424242',
)
class DuoReportTests(TestCase):

    def setUp(self):
        self.telegram = patch('analytics.duo_telegram.requests.post').start()
        self.telegram.return_value = _ok_response()
        self.addCleanup(patch.stopall)

    def _body(self, **overrides):
        body = {
            'install_id': INSTALL_ID,
            'kind': 'device',
            'app_version': '1.6.4',
            'build': '212',
            'os_version': '27.1',
            'model': 'iPhone19,4',
            'payload': {'screen': {'w': 1179, 'h': 2556}, 'lenses': ['wide', 'ultra']},
        }
        body.update(overrides)
        return body

    def _post(self, body):
        return self.client.post(URL, data=json.dumps(body), content_type='application/json')

    # --- success -------------------------------------------------------

    def test_success_stores_report_and_sends_message(self):
        resp = self._post(self._body(payload={'note': 'a <b> & c', 'имя': 'тест'}))

        self.assertEqual(resp.status_code, 201)
        report = DuoReport.objects.get()
        self.assertEqual(resp.json(), {'id': report.pk})
        self.assertEqual(str(report.install_id).upper(), INSTALL_ID)
        self.assertEqual(report.kind, 'device')
        self.assertEqual(report.model, 'iPhone19,4')
        self.assertEqual(report.payload, {'note': 'a <b> & c', 'имя': 'тест'})

        self.telegram.assert_called_once()
        args, kwargs = self.telegram.call_args
        self.assertTrue(args[0].endswith('/bottest-bot-token/sendMessage'))
        self.assertLessEqual(max(kwargs['timeout']), 5)
        sent = kwargs['json']
        self.assertEqual(sent['chat_id'], '424242')
        self.assertEqual(sent['parse_mode'], 'HTML')
        self.assertTrue(sent['text'].startswith(
            f'📱 Duo report #{report.pk} · device · iPhone19,4 · iOS 27.1 · '
            f'app 1.6.4 (212) · install 3f2504e0'
        ))
        # Pretty-printed, non-ASCII kept, HTML escaped inside <pre>.
        self.assertIn('<pre>{\n  "note": "a &lt;b&gt; &amp; c",\n  "имя": "тест"\n}</pre>', sent['text'])

    def test_unauthenticated_post_not_blocked_by_csrf(self):
        client = Client(enforce_csrf_checks=True)
        resp = client.post(URL, data=json.dumps(self._body()), content_type='application/json')
        self.assertEqual(resp.status_code, 201)

    def test_accepts_any_kind_matching_pattern(self):
        resp = self._post(self._body(kind='camera_v2'))
        self.assertEqual(resp.status_code, 201)

    def test_long_payload_goes_via_send_document(self):
        payload = {'formats': ['x' * 100 for _ in range(60)]}  # > 4096 chars, < 64 KB
        resp = self._post(self._body(kind='camera', payload=payload))

        self.assertEqual(resp.status_code, 201)
        report = DuoReport.objects.get()
        self.telegram.assert_called_once()
        args, kwargs = self.telegram.call_args
        self.assertTrue(args[0].endswith('/sendDocument'))
        self.assertEqual(kwargs['data']['chat_id'], '424242')
        self.assertTrue(kwargs['data']['caption'].startswith(f'📱 Duo report #{report.pk} · camera'))
        filename, content, content_type = kwargs['files']['document']
        self.assertEqual(filename, f'duo-report-{report.pk}-camera.json')
        self.assertEqual(content_type, 'application/json')
        self.assertEqual(json.loads(content.decode('utf-8')), payload)
        self.assertEqual(content.decode('utf-8'), json.dumps(payload, indent=2, ensure_ascii=False))

    def test_telegram_exception_still_returns_201(self):
        self.telegram.side_effect = requests.ConnectionError('telegram down')
        resp = self._post(self._body())
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(DuoReport.objects.count(), 1)

    def test_telegram_error_status_still_returns_201(self):
        bad = MagicMock(status_code=400, text='Bad Request: can\'t parse entities')
        self.telegram.return_value = bad
        resp = self._post(self._body())
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(DuoReport.objects.count(), 1)

    @override_settings(TELEGRAM_BOT_TOKEN='', TELEGRAM_OWNER_CHAT_ID='')
    def test_telegram_not_configured_still_stores(self):
        resp = self._post(self._body())
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(DuoReport.objects.count(), 1)
        self.telegram.assert_not_called()

    # --- validation ----------------------------------------------------

    def _assert_rejected(self, resp, field, code=400):
        self.assertEqual(resp.status_code, code)
        if field:
            self.assertIn(field, resp.json())
        self.assertEqual(DuoReport.objects.count(), 0)
        self.telegram.assert_not_called()

    def test_bad_uuid(self):
        self._assert_rejected(self._post(self._body(install_id='not-a-uuid')), 'install_id')

    def test_bad_kind_characters(self):
        self._assert_rejected(self._post(self._body(kind='Device')), 'kind')
        self._assert_rejected(self._post(self._body(kind='dev-ice')), 'kind')

    def test_kind_too_long(self):
        self._assert_rejected(self._post(self._body(kind='a' * 33)), 'kind')

    def test_empty_kind(self):
        self._assert_rejected(self._post(self._body(kind='')), 'kind')

    def test_missing_payload(self):
        body = self._body()
        del body['payload']
        self._assert_rejected(self._post(body), 'payload')

    def test_payload_not_an_object(self):
        self._assert_rejected(self._post(self._body(payload=[1, 2, 3])), 'payload')
        self._assert_rejected(self._post(self._body(payload='text')), 'payload')
        self._assert_rejected(self._post(self._body(payload=None)), 'payload')

    def test_payload_with_nul_character(self):
        self._assert_rejected(self._post(self._body(payload={'a': 'x\u0000y'})), 'payload')

    def test_string_fields_too_long(self):
        self._assert_rejected(self._post(self._body(model='m' * 65)), 'model')
        self._assert_rejected(self._post(self._body(build='1' * 17)), 'build')

    def test_missing_required_string(self):
        body = self._body()
        del body['os_version']
        self._assert_rejected(self._post(body), 'os_version')

    def test_malformed_json(self):
        resp = self.client.post(URL, data='{nope', content_type='application/json')
        self._assert_rejected(resp, None)

    def test_oversize_payload_returns_413(self):
        payload = {'blob': 'x' * (64 * 1024)}
        resp = self._post(self._body(payload=payload))
        self._assert_rejected(resp, None, code=413)
        self.assertEqual(resp.json()['error'], 'payload_too_large')

    # --- rate limit ----------------------------------------------------

    def _seed(self, count, install_id=INSTALL_ID, age=timedelta(0)):
        for _ in range(count):
            r = DuoReport.objects.create(
                install_id=install_id, kind='take', app_version='1.6.4', build='212',
                os_version='27.1', model='iPhone19,4', payload={},
            )
            if age:
                DuoReport.objects.filter(pk=r.pk).update(created_at=timezone.now() - age)

    def test_rate_limited_after_40_reports_in_24h(self):
        self._seed(40)
        resp = self._post(self._body())
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(DuoReport.objects.count(), 40)
        self.telegram.assert_not_called()

    def test_39_reports_still_allowed(self):
        self._seed(39)
        resp = self._post(self._body())
        self.assertEqual(resp.status_code, 201)

    def test_old_reports_do_not_count(self):
        self._seed(40, age=timedelta(hours=25))
        resp = self._post(self._body())
        self.assertEqual(resp.status_code, 201)

    def test_rate_limit_is_per_install(self):
        self._seed(40, install_id='11111111-1111-1111-1111-111111111111')
        resp = self._post(self._body())
        self.assertEqual(resp.status_code, 201)
