from django.contrib import admin

from .models import SyncRecord

@admin.register(SyncRecord)
class SyncRecordAdmin(admin.ModelAdmin):
    list_display = ['user', 'entity_type', 'client_id', 'status', 'attempts', 'updated_at']
    list_filter = ['school', 'entity_type', 'status']
    search_fields = ['client_id', 'user__username']
