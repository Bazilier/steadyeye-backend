from django.contrib import admin

from .models import AudioJob


@admin.register(AudioJob)
class AudioJobAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'app_user_id', 'tier', 'status', 'units', 'input_sec', 'length_sec',
        'error_message', 'deleted_at',
    )
    list_filter = ('status', 'tier', 'created_at')
    search_fields = ('app_user_id', 'device_id', 'production_uuid', 'ip')
    date_hierarchy = 'created_at'
    readonly_fields = tuple(f.name for f in AudioJob._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
