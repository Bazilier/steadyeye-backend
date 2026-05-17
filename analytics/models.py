from django.db import models


class AsaDailyMetrics(models.Model):
    """Apple Search Ads metrics, one row per
    day × level × campaign × country × search_term × keyword × match_type."""

    MATCH_TYPE_CHOICES = [
        ('EXACT', 'Exact'),
        ('BROAD', 'Broad'),
        ('SEARCH_MATCH', 'Search Match'),
    ]

    LEVEL_CHOICES = [
        ('campaign', 'Campaign'),
        ('ad_group', 'Ad Group'),
        ('keyword', 'Keyword'),
        ('search_term', 'Search Term'),
    ]

    date = models.DateField()
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES)
    campaign_name = models.CharField(max_length=200)
    country = models.CharField(max_length=2)
    search_term = models.CharField(max_length=500, blank=True)
    keyword = models.CharField(max_length=200, blank=True)
    match_type = models.CharField(max_length=20, choices=MATCH_TYPE_CHOICES, blank=True)
    spend = models.DecimalField(max_digits=10, decimal_places=2)
    impressions = models.IntegerField(default=0)
    taps = models.IntegerField(default=0)
    installs = models.IntegerField(default=0)
    installs_tap_through = models.IntegerField(default=0)
    installs_view_through = models.IntegerField(default=0)
    new_downloads_tap_through = models.IntegerField(default=0)
    new_downloads_view_through = models.IntegerField(default=0)
    new_downloads_total = models.IntegerField(default=0)
    redownloads_tap_through = models.IntegerField(default=0)
    redownloads_view_through = models.IntegerField(default=0)
    redownloads_total = models.IntegerField(default=0)
    ttr = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    cpt = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    cpa = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        unique_together = (
            ('date', 'level', 'campaign_name', 'country', 'search_term', 'keyword', 'match_type'),
        )

    def __str__(self):
        return f"{self.date} [{self.level}] {self.campaign_name} {self.keyword}"


class RcSubscriptionSnapshot(models.Model):
    """RevenueCat subscription state, one row per day per app_user_id."""

    SUBSCRIPTION_STATUS_CHOICES = [
        ('trial', 'Trial'),
        ('active', 'Active'),
        ('cancelled', 'Cancelled'),
        ('expired', 'Expired'),
        ('in_grace_period', 'In Grace Period'),
        ('none', 'None'),
    ]

    snapshot_date = models.DateField()
    app_user_id = models.CharField(max_length=200)
    country = models.CharField(max_length=2, null=True, blank=True)
    product_id = models.CharField(max_length=200, null=True, blank=True)
    subscription_status = models.CharField(
        max_length=50, choices=SUBSCRIPTION_STATUS_CHOICES
    )
    mrr_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    revenue_today_usd = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_discounted = models.BooleanField(default=False)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-snapshot_date']
        unique_together = (('snapshot_date', 'app_user_id'),)

    def __str__(self):
        return f"{self.snapshot_date} {self.app_user_id} ({self.subscription_status})"


class RcEvent(models.Model):
    """Raw RevenueCat webhook events, append-only."""

    event_id = models.CharField(max_length=200, unique=True, primary_key=True)
    event_type = models.CharField(max_length=100)
    occurred_at = models.DateTimeField(db_index=True)
    app_user_id = models.CharField(max_length=200, db_index=True)
    product_id = models.CharField(max_length=200, null=True, blank=True)
    country = models.CharField(max_length=2, null=True, blank=True)
    price_usd = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, null=True, blank=True)
    is_trial_conversion = models.BooleanField(default=False)
    is_renewal = models.BooleanField(default=False)
    raw_payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-occurred_at']

    def __str__(self):
        return f"{self.event_type} {self.app_user_id} @ {self.occurred_at}"


class Ga4DailyEvent(models.Model):
    """Aggregated GA4 event counts,
    one row per day × event × country × app_version × traffic_source."""

    date = models.DateField()
    event_name = models.CharField(max_length=100)
    country = models.CharField(max_length=2)
    app_version = models.CharField(max_length=20)
    traffic_source = models.CharField(max_length=50, null=True, blank=True)
    event_count = models.IntegerField(default=0)
    unique_users = models.IntegerField(default=0)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        unique_together = (
            ('date', 'event_name', 'country', 'app_version', 'traffic_source'),
        )

    def __str__(self):
        return f"{self.date} {self.event_name} [{self.country}] {self.app_version}"


class Ga4DailyFunnel(models.Model):
    """Pre-aggregated GA4 funnel snapshot, one row per day × country × app_version."""

    date = models.DateField()
    country = models.CharField(max_length=2, null=True, blank=True)
    app_version = models.CharField(max_length=20, null=True, blank=True)
    first_open = models.IntegerField(default=0)
    onboarding_completed = models.IntegerField(default=0)
    recording_started = models.IntegerField(default=0)
    recording_completed = models.IntegerField(default=0)
    paywall_shown = models.IntegerField(default=0)
    purchase_initiated = models.IntegerField(default=0)
    purchase_succeeded = models.IntegerField(default=0)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        unique_together = (('date', 'country', 'app_version'),)

    def __str__(self):
        return f"{self.date} funnel [{self.country or 'all'}] {self.app_version or 'all'}"


class RcActiveSubscriber(models.Model):
    """
    Snapshot of an active RC subscriber, imported from RC dashboard CSV export.
    One row per app_user_id. Re-imports update existing rows.
    """

    app_user_id = models.CharField(max_length=200, primary_key=True)

    # Project info
    project_id = models.CharField(max_length=50)
    project_name = models.CharField(max_length=100)
    app_id = models.CharField(max_length=50)
    app_name = models.CharField(max_length=100)

    # Activity timeline
    first_seen_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    first_purchase_at = models.DateTimeField(null=True, blank=True)
    trial_start_at = models.DateTimeField(null=True, blank=True)
    trial_end_at = models.DateTimeField(null=True, blank=True)
    most_recent_purchase_at = models.DateTimeField(null=True, blank=True)
    most_recent_renewal_at = models.DateTimeField(null=True, blank=True)
    latest_expiration_at = models.DateTimeField(null=True, blank=True)
    subscription_opt_out_at = models.DateTimeField(null=True, blank=True)
    trial_opt_out_at = models.DateTimeField(null=True, blank=True)
    most_recent_billing_issues_at = models.DateTimeField(null=True, blank=True)

    # Device / context
    last_seen_app_version = models.CharField(max_length=20, blank=True)
    last_seen_ip_country = models.CharField(max_length=2, blank=True, db_index=True)
    last_seen_platform = models.CharField(max_length=20, blank=True)
    last_seen_platform_version = models.CharField(max_length=200, blank=True)
    last_seen_sdk_version = models.CharField(max_length=20, blank=True)
    last_seen_locale = models.CharField(max_length=100, blank=True)

    # Attribution
    media_source = models.CharField(max_length=100, blank=True)
    campaign = models.CharField(max_length=200, blank=True)
    ad_group = models.CharField(max_length=200, blank=True)
    ad = models.CharField(max_length=200, blank=True)
    keyword = models.CharField(max_length=200, blank=True)
    creative = models.CharField(max_length=200, blank=True)

    # Subscription state
    latest_entitlement = models.CharField(max_length=50, blank=True)
    latest_entitlements = models.CharField(max_length=200, blank=True)
    latest_product = models.CharField(max_length=100, blank=True, db_index=True)
    all_purchased_product_ids = models.TextField(blank=True)
    is_rc_promo = models.BooleanField(default=False)
    status = models.CharField(max_length=30, blank=True, db_index=True)

    # Financial
    total_renewals = models.IntegerField(default=0)
    total_spent = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, blank=True)
    latest_store = models.CharField(max_length=30, blank=True)
    latest_store_country = models.CharField(max_length=2, blank=True)
    latest_auto_renew_intent = models.BooleanField(null=True, blank=True)

    # Offer
    latest_offer = models.CharField(max_length=200, blank=True)
    latest_offer_type = models.CharField(max_length=50, blank=True)
    latest_purchased_offering = models.CharField(max_length=100, blank=True)
    latest_ownership_type = models.CharField(max_length=30, blank=True)

    # Additional flags
    has_made_sandbox_purchase = models.BooleanField(default=False)
    has_made_a_non_subscription_purchase = models.BooleanField(default=False)

    # Email / phone (for support, not analytics)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=30, blank=True)

    # Price experiments
    price_experiment_id = models.CharField(max_length=50, blank=True)
    price_experiment_variant = models.CharField(max_length=50, blank=True)

    # Custom attributes (JSON-encoded string from RC)
    custom_attributes = models.TextField(blank=True)

    # Import metadata
    imported_at = models.DateTimeField(auto_now=True)
    source_file = models.CharField(max_length=200, blank=True, help_text="Filename of the import source")

    class Meta:
        ordering = ['-first_purchase_at']
        indexes = [
            models.Index(fields=['status', 'latest_product']),
            models.Index(fields=['last_seen_ip_country', 'status']),
            models.Index(fields=['first_purchase_at']),
        ]

    def __str__(self):
        return f"{self.app_user_id[:20]} | {self.last_seen_ip_country} | {self.latest_product} | {self.status}"
