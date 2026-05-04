from django.db import models


class User(models.Model):
    uuid = models.CharField(max_length=64, unique=True, db_index=True)
    email = models.EmailField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"User {self.uuid[:8]} ({self.email or 'no email'})"


class Message(models.Model):
    DIRECTION_CHOICES = [
        ('inbound', 'User → Founder'),
        ('outbound', 'Founder → User'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='messages')
    text = models.TextField()
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    metadata = models.JSONField(default=dict, blank=True)
    telegram_message_id = models.BigIntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.direction}: {self.text[:50]}"
