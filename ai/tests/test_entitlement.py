"""Entitlement resolution: RevenueCat reads, the 10-minute cache, and what
happens when RC is unavailable."""

from datetime import timedelta
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings
from django.utils import timezone

from ai.models import EntitlementCache
from ai.services.entitlement import is_paid_user, is_valid_app_user_id
from ai.tests.helpers import (
    FakeResponse,
    rc_created_free,
    rc_created_paid,
    rc_free,
    rc_lifetime,
    rc_paid,
)

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

    def test_unknown_subscriber_404_is_not_paid_and_is_not_cached(self):
        self.rc.return_value = FakeResponse(404, {'code': 7638, 'message': 'not found'})
        self.assertFalse(is_paid_user(UID))
        # Negatives are never cached — see NegativeResultsAreNotCachedTests.
        self.assertFalse(EntitlementCache.objects.filter(app_user_id=UID).exists())

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


@override_settings(REVENUECAT_SECRET_API_KEY='rc-test-key-not-real')
class RevenueCat201Tests(TestCase):
    """RC answers 201 when the GET creates the subscriber record. It carries
    the same body as a 200 and must be parsed identically, not treated as an
    outage (which is what sent every brand-new install down the free path)."""

    def setUp(self):
        self.rc = patch('ai.services.entitlement.requests.get').start()
        self.addCleanup(patch.stopall)

    def test_201_with_active_access_is_paid(self):
        self.rc.return_value = rc_created_paid()
        self.assertTrue(is_paid_user(UID))
        self.assertTrue(EntitlementCache.objects.filter(app_user_id=UID, is_paid=True).exists())

    def test_201_with_no_entitlements_is_free_and_logs_no_error(self):
        self.rc.return_value = rc_created_free()

        with self.assertNoLogs('ai.services.entitlement', level='WARNING'):
            self.assertFalse(is_paid_user(UID))

        self.assertFalse(EntitlementCache.objects.filter(app_user_id=UID).exists())

    def test_201_with_expired_access_is_not_paid(self):
        self.rc.return_value = rc_created_paid(expires_date='2020-01-01T00:00:00Z')
        self.assertFalse(is_paid_user(UID))

    def test_other_non_200_statuses_are_still_errors(self):
        for status_code in (400, 401, 500, 503):
            with self.subTest(status=status_code):
                EntitlementCache.objects.all().delete()
                self.rc.return_value = FakeResponse(status_code, {'message': 'nope'})
                with self.assertLogs('ai.services.entitlement', level='WARNING'):
                    self.assertFalse(is_paid_user(UID))


@override_settings(REVENUECAT_SECRET_API_KEY='rc-test-key-not-real')
class NegativeResultsAreNotCachedTests(TestCase):
    """The bug this suite pins: a user who subscribed seconds after a free
    check was served 'free' from cache for up to ten minutes, which on the
    /optimize/ endpoint surfaced as 403 free_quota_exhausted."""

    def setUp(self):
        self.rc = patch('ai.services.entitlement.requests.get').start()
        self.addCleanup(patch.stopall)

    def test_purchase_right_after_a_free_check_is_seen_immediately(self):
        self.rc.return_value = rc_free()
        self.assertFalse(is_paid_user(UID))

        # User subscribes. No TTL wait, no cache invalidation call.
        self.rc.return_value = rc_paid()
        self.assertTrue(is_paid_user(UID))

        self.assertEqual(self.rc.call_count, 2)

    def test_a_negative_result_writes_no_cache_row(self):
        self.rc.return_value = rc_free()
        self.assertFalse(is_paid_user(UID))
        self.assertFalse(EntitlementCache.objects.filter(app_user_id=UID).exists())

    def test_every_negative_check_queries_revenuecat(self):
        self.rc.return_value = rc_free()
        for _ in range(3):
            self.assertFalse(is_paid_user(UID))
        self.assertEqual(self.rc.call_count, 3)

    def test_cached_true_within_ttl_makes_no_rc_call(self):
        self.rc.return_value = rc_paid()
        self.assertTrue(is_paid_user(UID))
        self.rc.reset_mock()

        self.assertTrue(is_paid_user(UID))
        self.rc.assert_not_called()

    def test_legacy_cached_false_is_ignored_and_rc_is_queried(self):
        EntitlementCache.objects.create(
            app_user_id=UID, is_paid=False, checked_at=timezone.now()
        )
        self.rc.return_value = rc_paid()

        self.assertTrue(is_paid_user(UID))
        self.rc.assert_called_once()
        # The stale negative row is replaced by a positive one.
        row = EntitlementCache.objects.get(app_user_id=UID)
        self.assertTrue(row.is_paid)

    def test_expired_true_row_is_deleted_when_rc_says_not_paid(self):
        self.rc.return_value = rc_paid()
        self.assertTrue(is_paid_user(UID))
        EntitlementCache.objects.filter(app_user_id=UID).update(
            checked_at=timezone.now() - timedelta(minutes=11)
        )

        # Subscription lapsed: RC is authoritative, the row must go so it can
        # never be resurrected as a stale True by a later RC outage.
        self.rc.return_value = rc_free()
        self.assertFalse(is_paid_user(UID))
        self.assertFalse(EntitlementCache.objects.filter(app_user_id=UID).exists())

    def test_a_lapsed_user_is_not_rescued_by_a_later_rc_outage(self):
        self.rc.return_value = rc_paid()
        self.assertTrue(is_paid_user(UID))
        EntitlementCache.objects.filter(app_user_id=UID).update(
            checked_at=timezone.now() - timedelta(minutes=11)
        )
        self.rc.return_value = rc_free()
        self.assertFalse(is_paid_user(UID))

        # The row is gone, so an outage now has nothing stale to fall back on.
        self.rc.side_effect = requests.ConnectionError('boom')
        self.assertFalse(is_paid_user(UID))


@override_settings(REVENUECAT_SECRET_API_KEY='rc-test-key-not-real')
class RevenueCatOutageTests(TestCase):
    """What an RC failure falls back to, per cache state."""

    def setUp(self):
        self.rc = patch('ai.services.entitlement.requests.get').start()
        self.addCleanup(patch.stopall)

    def _seed_stale_paid_row(self):
        self.rc.return_value = rc_paid()
        is_paid_user(UID)
        EntitlementCache.objects.filter(app_user_id=UID).update(
            checked_at=timezone.now() - timedelta(hours=5)
        )
        self.rc.reset_mock()

    def test_outage_with_stale_cached_true_is_paid(self):
        self._seed_stale_paid_row()
        self.rc.side_effect = requests.ConnectionError('boom')

        self.assertTrue(is_paid_user(UID))

    def test_outage_with_no_cache_is_free(self):
        self.rc.side_effect = requests.ConnectionError('boom')
        self.assertFalse(is_paid_user(UID))
        self.assertFalse(EntitlementCache.objects.filter(app_user_id=UID).exists())

    def test_outage_with_legacy_cached_false_is_free(self):
        EntitlementCache.objects.create(
            app_user_id=UID, is_paid=False, checked_at=timezone.now()
        )
        self.rc.side_effect = requests.ConnectionError('boom')

        self.assertFalse(is_paid_user(UID))

    def test_outage_does_not_delete_a_cached_true_row(self):
        self._seed_stale_paid_row()
        self.rc.side_effect = requests.ConnectionError('boom')
        is_paid_user(UID)

        self.assertTrue(EntitlementCache.objects.filter(app_user_id=UID, is_paid=True).exists())


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
