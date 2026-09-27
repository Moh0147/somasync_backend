from django.contrib import admin

from .models import ClassLevel, LearningObjective, Subject, Topic


@admin.register(ClassLevel)
class ClassLevelAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'order', 'is_active']
    ordering = ['order']


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'school', 'code', 'is_active']
    list_filter = ['school', 'is_active']
    search_fields = ['name', 'code']


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ['title', 'subject', 'class_level', 'school', 'is_active']
    list_filter = ['school', 'subject', 'class_level', 'is_active']
    search_fields = ['title']


@admin.register(LearningObjective)
class LearningObjectiveAdmin(admin.ModelAdmin):
    list_display = ['topic', 'description', 'school', 'is_active']
    list_filter = ['school', 'is_active']
    search_fields = ['description']
