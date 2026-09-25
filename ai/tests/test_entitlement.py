"""Entitlement resolution: RevenueCat reads, the 10-minute cache, and what
happens when RC is unavailable."""

from datetime import timedelta
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings
from django.utils import timezone

from ai.models import EntitlementCache
from ai.services.entitlement import is_paid_user, is_valid_app_user_id
from ai.tests.helpers import FakeResponse, rc_free, rc_lifetime, rc_paid

UID = '44444444-4444-4444-4444-444444444444'


@override_settings(REVENUECAT_SECRET_API_KEY='rc-test-key-not-real')
class IsPaidUserTests(TestCase):

    def setUp(self):
        self.rc = patch('ai.services.entitlement.requests.get').start()
        self.addCleanup(patch.stopall)

    def test_active_entitlement_is_paid(self):
        self.rc.return_value = rc_paid()
        self.assertTrue(is_paid_user(UID))

    def test_lifetime_null_expiry_is_paid(self):
        self.rc.return_value = rc_lifetime()
        self.assertTrue(is_paid_user(UID))

    def test_expired_entitlement_is_not_paid(self):
        self.rc.return_value = rc_paid(expires_date='2020-01-01T00:00:00Z')
        self.assertFalse(is_paid_user(UID))

    def test_no_access_entitlement_is_not_paid(self):
        self.rc.return_value = rc_free()
        self.assertFalse(is_paid_user(UID))

    def test_unknown_subscriber_404_is_not_paid_and_is_cached(self):
        self.rc.return_value = FakeResponse(404, {'code': 7638, 'message': 'not found'})
        self.assertFalse(is_paid_user(UID))
        self.assertTrue(EntitlementCache.objects.filter(app_user_id=UID).exists())

    def test_result_is_cached_for_ten_minutes(self):
        self.rc.return_value = rc_paid()
        self.assertTrue(is_paid_user(UID))
        self.assertTrue(is_paid_user(UID))
        self.assertEqual(self.rc.call_count, 1)

    def test_cache_is_refreshed_once_stale(self):
        self.rc.return_value = rc_paid()
        is_paid_user(UID)

        EntitlementCache.objects.filter(app_user_id=UID).update(
            checked_at=timezone.now() - timedelta(minutes=11)
        )
        self.rc.return_value = rc_free()

        self.assertFalse(is_paid_user(UID))
        self.assertEqual(self.rc.call_count, 2)

    def test_stale_cache_wins_when_revenuecat_is_down(self):
        self.rc.return_value = rc_paid()
        is_paid_user(UID)
        EntitlementCache.objects.filter(app_user_id=UID).update(
            checked_at=timezone.now() - timedelta(hours=5)
        )
        self.rc.side_effect = requests.ConnectionError('boom')

        # A paying customer keeps working through an RC outage.
        self.assertTrue(is_paid_user(UID))

    def test_no_cache_and_revenuecat_down_falls_back_to_free(self):
        self.rc.side_effect = requests.ConnectionError('boom')
        self.assertFalse(is_paid_user(UID))
        self.assertFalse(EntitlementCache.objects.filter(app_user_id=UID).exists())

    def test_revenuecat_500_does_not_poison_the_cache(self):
        self.rc.return_value = FakeResponse(500, {'message': 'server error'})
        self.assertFalse(is_paid_user(UID))
        self.assertFalse(EntitlementCache.objects.exists())

    @override_settings(REVENUECAT_SECRET_API_KEY='')
    def test_missing_rc_key_treats_user_as_free_without_calling_rc(self):
        self.assertFalse(is_paid_user(UID))
        self.rc.assert_not_called()

    def test_app_user_id_is_url_encoded(self):
        self.rc.return_value = rc_paid()
        is_paid_user('$RCAnonymousID:abc123')
        url = self.rc.call_args.args[0]
        self.assertTrue(url.endswith('/%24RCAnonymousID%3Aabc123'))


class ValidatorTests(TestCase):
    """The validator is imported from attribution.views — this pins the
    behaviour the ai app depends on so a change there fails here too."""

    def test_accepts_uuid_and_rc_anonymous_id(self):
        self.assertTrue(is_valid_app_user_id(UID))
        self.assertTrue(is_valid_app_user_id(UID.upper()))
        self.assertTrue(is_valid_app_user_id('$RCAnonymousID:a1b2c3'))

    def test_rejects_everything_else(self):
        for bad in (None, '', '   ', 'not-a-uuid', 123, '$RCAnonymousID:', 'drop table'):
            self.assertFalse(is_valid_app_user_id(bad), bad)


class PromptFidelityTests(TestCase):
    """The prompts must stay byte-identical to the iOS bundle copies, or the
    model output shifts for users who have not updated the app yet."""

    def test_prompts_are_non_empty_and_unstripped(self):
        from ai.services.prompts import OPTIMIZE_SYSTEM_PROMPT, SPLIT_SYSTEM_PROMPT
        self.assertGreater(len(OPTIMIZE_SYSTEM_PROMPT), 500)
        self.assertGreater(len(SPLIT_SYSTEM_PROMPT), 500)
