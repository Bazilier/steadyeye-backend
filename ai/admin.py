from django.contrib import admin

from .models import AITextLog, AIUsage, EntitlementCache


@admin.register(AIUsage)
class AIUsageAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'app_user_id', 'endpoint', 'is_paid', 'status',
        'input_tokens', 'output_tokens',
    )
    list_filter = ('endpoint', 'status', 'is_paid', 'created_at')
    search_fields = ('app_user_id', 'ip')
    date_hierarchy = 'created_at'
    readonly_fields = tuple(f.name for f in AIUsage._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AITextLog)
class AITextLogAdmin(admin.ModelAdmin):
    list_display = (
        'created_at', 'endpoint', 'is_paid', 'accept_language',
        'input_length', 'input_preview',
    )
    list_filter = ('endpoint', 'is_paid', 'created_at')
    search_fields = ('input_text', 'output_text')
    date_hierarchy = 'created_at'
    readonly_fields = tuple(f.name for f in AITextLog._meta.fields)

    @admin.display(description='Input length')
    def input_length(self, obj):
        return len(obj.input_text)

    @admin.display(description='Input preview')
    def input_preview(self, obj):
        return obj.input_text[:120]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(EntitlementCache)
class EntitlementCacheAdmin(admin.ModelAdmin):
    list_display = ('app_user_id', 'is_paid', 'checked_at')
    list_filter = ('is_paid',)
    search_fields = ('app_user_id',)
    readonly_fields = ('app_user_id', 'is_paid', 'checked_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
