"""Endpoint behaviour for /api/v1/audio/enhance/.

Every test patches both outbound callers (RevenueCat and Auphonic); a real
socket call would fail the suite rather than quietly spend credits.
"""

from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from ai.tests.helpers import FakeResponse, rc_free, rc_paid
from audio.models import AudioJob
from audio.services import auphonic_client

PAID_UUID = '11111111-1111-1111-1111-111111111111'
OTHER_UUID = '33333333-3333-3333-3333-333333333333'
ENHANCE_URL = '/api/v1/audio/enhance/'
FIXTURE = Path(__file__).parent / 'fixtures' / 'two_seconds.m4a'
PRODUCTION_UUID = 'KKw7AxpLrDBQKLVnQCBtCh'
DOWNLOAD_URL = f'https://auphonic.com/api/download/audio-result/{PRODUCTION_UUID}/out.m4a'


def rc_trial():
    return FakeResponse(200, {'subscriber': {
        'entitlements': {'access': {
            'expires_date': '2099-01-01T00:00:00Z', 'product_identifier': 'annual_trial',
        }},
        'subscriptions': {'annual_trial': {'period_type': 'trial'}},
    }})


def auphonic_ok(data=None):
    return FakeResponse(200, {'status_code': 200, 'error_code': None, 'data': data or {}})


def production(status_code, length=2.0, output_files=None):
    if output_files is None:
        output_files = [{'format': 'alac', 'ending': 'm4a', 'download_url': DOWNLOAD_URL}]
    return auphonic_ok({
        'uuid': PRODUCTION_UUID, 'status': status_code, 'length': length,
        'output_files': output_files, 'error_message': '',
    })


@override_settings(
    AUPHONIC_API_KEY='auphonic-test-key-not-real',
    AUDIO_ENHANCE_ENABLED=True,
    REVENUECAT_SECRET_API_KEY='rc-test-key-not-real',
    AUDIO_ENHANCE_PAID_MONTHLY_UNITS=10,
    AUDIO_ENHANCE_TRIAL_UNITS=3,
    AUDIO_ENHANCE_IP_DAILY_UNITS=15,
    AUDIO_ENHANCE_GLOBAL_DAILY_UNITS=15,
    AUDIO_ENHANCE_GLOBAL_MONTHLY_UNITS=40,
)
class AudioEnhanceTestCase(TestCase):

    def setUp(self):
        self.auphonic = patch('audio.services.auphonic_client.requests.request').start()
        self.revenuecat = patch('ai.services.entitlement.requests.get').start()
        self.revenuecat.return_value = rc_paid()
        self.auphonic.side_effect = self._auphonic_started
        self.addCleanup(patch.stopall)

    @staticmethod
    def _auphonic_started(method, url, **kwargs):
        if method == 'post' and url.endswith('/productions.json'):
            return auphonic_ok({'uuid': PRODUCTION_UUID})
        return auphonic_ok()

    def upload(self, user_id=PAID_UUID, content=None, name='audio', request_id=None):
        data = {}
        if request_id is not None:
            data['request_id'] = request_id
        if name:
            body = FIXTURE.read_bytes() if content is None else content
            data[name] = SimpleUploadedFile('a.m4a', body, content_type='audio/mp4')
        headers = {'x-app-user-id': user_id} if user_id else {}
        return self.client.post(ENHANCE_URL, data=data, headers=headers)

    def get(self, path, user_id=PAID_UUID):
        return self.client.get(path, headers={'x-app-user-id': user_id})

    def start_job(self):
        resp = self.upload()
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()['id']

    # --- POST ---------------------------------------------------------------

    def test_start_creates_uploads_and_starts_with_fixed_settings(self):
        resp = self.upload()

        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body['status'], 'processing')
        job = AudioJob.objects.get(id=body['id'])
        self.assertEqual(job.production_uuid, PRODUCTION_UUID)
        self.assertAlmostEqual(job.input_sec, 2.0, delta=0.2)

        calls = self.auphonic.call_args_list
        self.assertEqual([c.args[0] for c in calls], ['post', 'post', 'post'])
        self.assertTrue(calls[1].args[1].endswith(f'/production/{PRODUCTION_UUID}/upload.json'))
        self.assertTrue(calls[2].args[1].endswith(f'/production/{PRODUCTION_UUID}/start.json'))
        sent = calls[0].kwargs['json']
        self.assertEqual(sent['algorithms'], auphonic_client.ALGORITHMS)
        self.assertEqual(sent['output_files'], [{'format': 'alac', 'ending': 'm4a'}])
        self.assertEqual(
            calls[0].kwargs['headers']['Authorization'], 'bearer auphonic-test-key-not-real'
        )

    def test_every_cutter_is_off(self):
        algorithms = auphonic_client.ALGORITHMS
        self.assertIs(algorithms['cutter'], False)
        for key in ('silence_cutter', 'filler_cutter', 'cough_cutter', 'music_cutter'):
            self.assertIs(algorithms[key], False, key)
        self.assertEqual(algorithms['cut_mode'], 'export_uncut_audio')

    def test_invalid_user_id(self):
        self.assertEqual(self.upload(user_id='nope').status_code, 400)
        self.assertEqual(self.upload(user_id=None).status_code, 400)

    def test_disabled_or_missing_key(self):
        with self.settings(AUDIO_ENHANCE_ENABLED=False):
            self.assertEqual(self.upload().status_code, 503)
        with self.settings(AUPHONIC_API_KEY=''):
            self.assertEqual(self.upload().status_code, 503)
        self.auphonic.assert_not_called()

    def test_missing_file(self):
        self.assertEqual(self.upload(name=None).status_code, 400)

    def test_not_audio(self):
        resp = self.upload(content=b'definitely not an mp4')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error'], 'invalid_audio')

    def test_too_long(self):
        with patch('audio.views.m4a_duration_sec', return_value=15 * 60 + 1):
            resp = self.upload()
        self.assertEqual(resp.status_code, 413)
        self.assertEqual(resp.json()['error'], 'input_too_long')

    def test_free_user_is_refused(self):
        self.revenuecat.return_value = rc_free()
        resp = self.upload()
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['error'], 'subscription_required')
        self.auphonic.assert_not_called()

    def test_revenuecat_down_refuses(self):
        self.revenuecat.return_value = FakeResponse(500, {})
        resp = self.upload()
        self.assertEqual(resp.status_code, 502)
        self.auphonic.assert_not_called()

    # --- quotas ---------------------------------------------------------------

    def _jobs(self, n, units=1, user=PAID_UUID, **fields):
        for _ in range(n):
            AudioJob.objects.create(app_user_id=user, input_sec=60, units=units, **fields)

    def test_short_clip_is_one_unit_and_remaining_is_reported(self):
        body = self.upload().json()
        self.assertEqual(AudioJob.objects.get().units, 1)
        self.assertEqual((body['limit'], body['remaining']), (10, 9))

    def test_units_round_up_per_three_minutes(self):
        with patch('audio.views.m4a_duration_sec', return_value=7 * 60):
            self.upload()
        self.assertEqual(AudioJob.objects.get().units, 3)

    def test_paid_monthly_limit(self):
        self._jobs(10)
        resp = self.upload()
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()['error'], 'monthly_limit')
        self.auphonic.assert_not_called()

    def test_a_long_clip_must_fit_in_what_is_left(self):
        self._jobs(8)
        with patch('audio.views.m4a_duration_sec', return_value=7 * 60):
            self.assertEqual(self.upload().status_code, 429)

    def test_failed_jobs_and_last_month_dont_count(self):
        self._jobs(10, status='error')
        old = AudioJob.objects.create(app_user_id=PAID_UUID, input_sec=60, units=10)
        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        AudioJob.objects.filter(id=old.id).update(created_at=month_start - timedelta(days=1))
        self.assertEqual(self.upload().status_code, 201)

    def test_trial_gets_its_own_small_allowance(self):
        self.revenuecat.return_value = rc_trial()
        for _ in range(3):
            self.assertEqual(self.upload().status_code, 201)
        resp = self.upload()
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()['error'], 'trial_limit')
        self.assertTrue(all(job.is_trial for job in AudioJob.objects.all()))

    def test_trial_use_does_not_eat_the_paid_month(self):
        self._jobs(3, is_trial=True)
        body = self.upload().json()
        self.assertEqual(body['remaining'], 9)

    def test_ip_daily_limit(self):
        self._jobs(15, user=OTHER_UUID, ip='127.0.0.1')
        resp = self.upload()
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()['error'], 'rate_limited')

    def test_global_daily_limit(self):
        self._jobs(15, user=OTHER_UUID, ip='10.0.0.1')
        resp = self.upload()
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()['error'], 'rate_limited')

    def test_global_monthly_limit(self):
        AudioJob.objects.create(app_user_id=OTHER_UUID, input_sec=60, units=40, ip='10.0.0.1')
        old = AudioJob.objects.get()
        yesterday = timezone.now() - timedelta(days=1)
        if yesterday.month == timezone.now().month:
            AudioJob.objects.filter(id=old.id).update(created_at=yesterday)
        resp = self.upload()
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()['error'], 'rate_limited')

    def test_upstream_failure_marks_job_and_cleans_up(self):
        def fail_upload(method, url, **kwargs):
            if url.endswith('/productions.json'):
                return auphonic_ok({'uuid': PRODUCTION_UUID})
            if url.endswith('/upload.json'):
                return FakeResponse(500, {'error_message': 'boom'})
            return auphonic_ok()
        self.auphonic.side_effect = fail_upload

        resp = self.upload()

        self.assertEqual(resp.status_code, 502)
        job = AudioJob.objects.get()
        self.assertEqual(job.status, 'error')
        last = self.auphonic.call_args_list[-1]
        self.assertEqual(last.args[0], 'delete')

    # --- GET status ---------------------------------------------------------

    def test_status_processing_then_done(self):
        job_id = self.start_job()
        self.auphonic.side_effect = None

        self.auphonic.return_value = production(4)
        body = self.get(f'{ENHANCE_URL}{job_id}/').json()
        self.assertEqual(body, {'id': job_id, 'status': 'processing', 'length_sec': None})

        AudioJob.objects.filter(id=job_id).update(checked_at=None)
        self.auphonic.return_value = production(3, length=2.0)
        body = self.get(f'{ENHANCE_URL}{job_id}/').json()
        self.assertEqual(body['status'], 'done')
        self.assertEqual(body['length_sec'], 2.0)
        self.assertEqual(AudioJob.objects.get(id=job_id).result_url, DOWNLOAD_URL)

    def test_status_is_throttled(self):
        job_id = self.start_job()
        self.auphonic.side_effect = None
        self.auphonic.return_value = production(4)
        calls_before = self.auphonic.call_count
        self.get(f'{ENHANCE_URL}{job_id}/')
        self.get(f'{ENHANCE_URL}{job_id}/')
        self.assertEqual(self.auphonic.call_count, calls_before + 1)

    def test_status_error(self):
        job_id = self.start_job()
        self.auphonic.side_effect = None
        self.auphonic.return_value = production(2)
        self.assertEqual(self.get(f'{ENHANCE_URL}{job_id}/').json()['status'], 'error')

    def test_done_without_alac_output_is_an_error(self):
        job_id = self.start_job()
        self.auphonic.side_effect = None
        self.auphonic.return_value = production(3, output_files=[])
        self.assertEqual(self.get(f'{ENHANCE_URL}{job_id}/').json()['status'], 'error')

    def test_status_upstream_failure_keeps_processing(self):
        job_id = self.start_job()
        self.auphonic.side_effect = None
        self.auphonic.return_value = FakeResponse(503, {})
        self.assertEqual(self.get(f'{ENHANCE_URL}{job_id}/').json()['status'], 'processing')

    def test_someone_elses_job_is_not_found(self):
        job_id = self.start_job()
        self.assertEqual(self.get(f'{ENHANCE_URL}{job_id}/', user_id=OTHER_UUID).status_code, 404)
        self.assertEqual(
            self.get(f'{ENHANCE_URL}{job_id}/result/', user_id=OTHER_UUID).status_code, 404
        )

    # --- GET result / DELETE ------------------------------------------------

    def _done_job(self):
        return AudioJob.objects.create(
            app_user_id=PAID_UUID, input_sec=2.0, length_sec=2.0, status='done',
            production_uuid=PRODUCTION_UUID, result_url=DOWNLOAD_URL,
        )

    def test_result_streams_file(self):
        job = self._done_job()
        upstream = MagicMock(status_code=200, headers={'Content-Length': '6'})
        upstream.iter_content.return_value = iter([b'abc', b'def'])
        self.auphonic.side_effect = None
        self.auphonic.return_value = upstream

        resp = self.get(f'{ENHANCE_URL}{job.id}/result/')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b''.join(resp.streaming_content), b'abcdef')
        self.assertEqual(resp['Content-Type'], 'audio/mp4')
        self.assertEqual(resp['Content-Length'], '6')
        self.assertEqual(self.auphonic.call_args.args[:2], ('get', DOWNLOAD_URL))
        self.assertIs(self.auphonic.call_args.kwargs['stream'], True)
        upstream.close.assert_called()

    def test_result_not_ready(self):
        job_id = self.start_job()
        self.assertEqual(self.get(f'{ENHANCE_URL}{job_id}/result/').status_code, 409)

    def test_delete_removes_production_and_result_is_gone(self):
        job = self._done_job()
        self.auphonic.side_effect = None
        self.auphonic.return_value = FakeResponse(204, None, raw_text='')

        resp = self.client.delete(
            f'{ENHANCE_URL}{job.id}/', headers={'x-app-user-id': PAID_UUID}
        )

        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self.auphonic.call_args.args[0], 'delete')
        self.assertIsNotNone(AudioJob.objects.get(id=job.id).deleted_at)
        self.assertEqual(self.get(f'{ENHANCE_URL}{job.id}/result/').status_code, 410)
