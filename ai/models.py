from django.db import models


class AIUsage(models.Model):
    """One row per request that reached the proxy with a usable identity.

    Rows are written for rejected requests too (status='rejected'), because the
    IP hourly counter and the audit trail both need to see attempts, not just
    the calls that made it to Anthropic. Token counts stay 0 unless Anthropic
    actually answered.
    """

    ENDPOINT_CHOICES = [
        ('optimize', 'Optimize'),
        ('split', 'Split'),
    ]

    STATUS_CHOICES = [
        ('ok', 'OK'),
        ('upstream_error', 'Upstream error'),
        ('rejected', 'Rejected'),
    ]

    app_user_id = models.CharField(max_length=200, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    endpoint = models.CharField(max_length=20, choices=ENDPOINT_CHOICES)
    is_paid = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    reject_reason = models.CharField(max_length=50, blank=True)
    input_chars = models.IntegerField(default=0)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    model = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.created_at} {self.endpoint} {self.status} {self.app_user_id[:8]}"


class EntitlementCache(models.Model):
    """Last known RevenueCat entitlement state for an app_user_id.

    Read through with a 10-minute TTL so a burst of optimize calls during a
    bulk import costs one RC round trip, not one per script. A stale row is
    still better than nothing when RC is down — see services.entitlement.
    """

    app_user_id = models.CharField(max_length=200, unique=True, db_index=True)
    is_paid = models.BooleanField(default=False)
    checked_at = models.DateTimeField()

    def __str__(self):
        return f"{self.app_user_id[:8]} paid={self.is_paid} @ {self.checked_at}"
