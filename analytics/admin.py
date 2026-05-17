from django.contrib import admin

from .models import (
    AsaDailyMetrics,
    Ga4DailyEvent,
    Ga4DailyFunnel,
    RcEvent,
    RcSubscriptionSnapshot,
)


@admin.register(AsaDailyMetrics)
class AsaDailyMetricsAdmin(admin.ModelAdmin):
    list_display = (
        'date', 'campaign_name', 'country', 'keyword', 'match_type',
        'spend', 'impressions', 'taps', 'installs', 'synced_at',
    )
    list_filter = ('date', 'country', 'match_type', 'campaign_name')
    search_fields = ('campaign_name', 'keyword', 'search_term')
    date_hierarchy = 'date'


@admin.register(RcSubscriptionSnapshot)
class RcSubscriptionSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        'snapshot_date', 'app_user_id', 'country', 'product_id',
        'subscription_status', 'mrr_usd', 'revenue_today_usd',
        'is_discounted', 'synced_at',
    )
    list_filter = ('snapshot_date', 'subscription_status', 'country', 'is_discounted')
    search_fields = ('app_user_id', 'product_id')
    date_hierarchy = 'snapshot_date'


@admin.register(RcEvent)
class RcEventAdmin(admin.ModelAdmin):
    list_display = (
        'event_id', 'event_type', 'occurred_at', 'app_user_id',
        'product_id', 'country', 'price_usd', 'currency',
        'is_trial_conversion', 'is_renewal', 'received_at',
    )
    list_filter = ('event_type', 'country', 'is_trial_conversion', 'is_renewal')
    search_fields = ('event_id', 'app_user_id', 'product_id')
    date_hierarchy = 'occurred_at'


@admin.register(Ga4DailyEvent)
class Ga4DailyEventAdmin(admin.ModelAdmin):
    list_display = (
        'date', 'event_name', 'country', 'app_version',
        'traffic_source', 'event_count', 'unique_users', 'synced_at',
    )
    list_filter = ('date', 'event_name', 'country', 'app_version', 'traffic_source')
    search_fields = ('event_name',)
    date_hierarchy = 'date'


@admin.register(Ga4DailyFunnel)
class Ga4DailyFunnelAdmin(admin.ModelAdmin):
    list_display = (
        'date', 'country', 'app_version', 'first_open',
        'onboarding_completed', 'recording_started', 'recording_completed',
        'paywall_shown', 'purchase_initiated', 'purchase_succeeded', 'synced_at',
    )
    list_filter = ('date', 'country', 'app_version')
    date_hierarchy = 'date'
