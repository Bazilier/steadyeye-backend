from django.contrib import admin

from .models import Message, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'email', 'created_at')
    search_fields = ('uuid', 'email')
    readonly_fields = ('created_at', 'last_seen_metadata')
    ordering = ('-created_at',)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'direction', 'short_text', 'telegram_message_id', 'created_at')
    list_filter = ('direction', 'created_at')
    search_fields = ('text', 'user__uuid', 'user__email')
    readonly_fields = ('created_at', 'metadata', 'telegram_message_id')
    ordering = ('-created_at',)
    autocomplete_fields = ('user',)

    @admin.display(description='Text')
    def short_text(self, obj):
        return obj.text[:80]
