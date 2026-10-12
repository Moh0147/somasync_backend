from django.contrib import admin

from .models import LessonProgress

@admin.register(LessonProgress)
class LessonProgressAdmin(admin.ModelAdmin):
    list_display = ['student', 'lesson', 'progress_percentage', 'completed', 'last_accessed_at']
    list_filter = ['school', 'completed']
    search_fields = ['student__username', 'lesson__title']
