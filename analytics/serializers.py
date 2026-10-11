import json

from rest_framework import serializers


class DuoReportSerializer(serializers.Serializer):
    """Inbound iPhone Duo diagnostic report. The payload size cap is checked
    in the view, not here, so that it can answer 413 rather than 400."""

    install_id = serializers.UUIDField()
    kind = serializers.RegexField(r'\A[a-z0-9_]+\Z', max_length=32)
    app_version = serializers.CharField(max_length=32)
    build = serializers.CharField(max_length=16)
    os_version = serializers.CharField(max_length=32)
    model = serializers.CharField(max_length=64)
    payload = serializers.JSONField()

    def validate_payload(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError('payload must be a JSON object.')
        # PostgreSQL jsonb rejects U+0000; catch it here as a 400, not a 500.
        if '\\u0000' in json.dumps(value):
            raise serializers.ValidationError('payload must not contain NUL characters.')
        return value
