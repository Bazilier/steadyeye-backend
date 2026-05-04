from rest_framework import serializers

from .models import Message


class InboundMessageSerializer(serializers.Serializer):
    user_uuid = serializers.CharField(max_length=64)
    text = serializers.CharField()
    metadata = serializers.JSONField(required=False, default=dict)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ['id', 'text', 'direction', 'created_at']
