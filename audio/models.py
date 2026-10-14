import uuid

from django.db import models


class AudioJob(models.Model):
    """One "Studio Sound" request: an audio track sent to Auphonic.

    The id is what the client polls with; production_uuid is Auphonic's and
    never leaves the server. input_sec is read from the uploaded file itself,
    not taken from the client, and is what the quotas count (Auphonic bills
    input duration with a 3-minute minimum per production).
    """

    STATUS_CHOICES = [
        ('processing', 'Processing'),
        ('done', 'Done'),
        ('error', 'Error'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    app_user_id = models.CharField(max_length=200, db_index=True)
    # Client-chosen id of one "enhance" attempt. A retried upload whose first
    # response was lost carries the same id and gets the same job back,
    # instead of a second production billed by Auphonic.
    request_id = models.CharField(max_length=64, blank=True, db_index=True)
    production_uuid = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='processing')
    input_sec = models.FloatField()
    # What the quotas count: one per started 3 minutes of input, matching
    # Auphonic's 3-minute minimum charge per production.
    units = models.PositiveSmallIntegerField(default=1)
    TIER_CHOICES = [
        ('free', 'Free'),
        ('trial', 'Trial'),
        ('paid', 'Paid'),
    ]
    # The user's RevenueCat tier when the job started: which allowance it
    # counts against (free: once ever, trial: per trial, paid: per month).
    tier = models.CharField(max_length=10, choices=TIER_CHOICES, default='paid')
    # Keychain id of the device (X-Device-Id). Survives a reinstall, which
    # mints a new anonymous RevenueCat id, so the free try can't be renewed
    # by reinstalling.
    device_id = models.CharField(max_length=64, blank=True, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    # Output length as Auphonic reports it. Should equal input_sec: every
    # cutting algorithm is off, so the client can lay it under the video 1:1.
    length_sec = models.FloatField(null=True, blank=True)
    # Auphonic download URL. Not a secret on its own: fetching it needs the
    # API key, so it is only ever fetched server-side.
    result_url = models.URLField(max_length=500, blank=True)
    error_message = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    # Set once the production (input and result) is deleted at Auphonic.
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.created_at} {self.status} {self.input_sec:.1f}s {self.app_user_id[:8]}"
