from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'role', 'admin_scope', 'is_active', 'is_staff']
    list_filter = ['role', 'admin_scope', 'is_active', 'is_staff']
    fieldsets = BaseUserAdmin.fieldsets + (
        ('SomaSync', {'fields': ('role', 'admin_scope', 'phone_number')}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('SomaSync', {'fields': ('role', 'admin_scope', 'phone_number')}),
    )
