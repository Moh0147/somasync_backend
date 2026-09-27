from django.contrib import admin

from .models import SchoolClass, StudentClassMembership, TeacherClassMembership


@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = ['name', 'school', 'class_level', 'academic_year', 'class_code', 'is_active']
    list_filter = ['school', 'class_level', 'academic_year', 'is_active']
    search_fields = ['name', 'class_code']


@admin.register(TeacherClassMembership)
class TeacherClassMembershipAdmin(admin.ModelAdmin):
    list_display = ['teacher', 'school_class', 'assigned_at']


@admin.register(StudentClassMembership)
class StudentClassMembershipAdmin(admin.ModelAdmin):
    list_display = ['student', 'school_class', 'joined_at']
