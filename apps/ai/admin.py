from django.contrib import admin

from .models import AIExplanationRequest, MistakeExplanationCache


@admin.register(AIExplanationRequest)
class AIExplanationRequestAdmin(admin.ModelAdmin):
    list_display = ['student', 'student_answer', 'status', 'attempts', 'created_at']
    list_filter = ['school', 'status']
    search_fields = ['student__username']


@admin.register(MistakeExplanationCache)
class MistakeExplanationCacheAdmin(admin.ModelAdmin):
    list_display = ['question', 'wrong_answer_signature', 'hit_count', 'updated_at']
    search_fields = ['question__text']
