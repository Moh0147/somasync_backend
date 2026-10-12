from django.contrib import admin

from .models import (
    Lesson, LessonAssignment, LessonAttachment, LessonSection, SectionContentBlock, SectionMiniQuiz,
    StudentSectionProgress,
)

@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ['title', 'school', 'subject', 'class_level', 'status', 'created_by', 'published_at']
    list_filter = ['school', 'status', 'subject', 'class_level']
    search_fields = ['title']

@admin.register(LessonAssignment)
class LessonAssignmentAdmin(admin.ModelAdmin):
    list_display = ['lesson', 'school_class', 'assigned_by', 'assigned_at']

@admin.register(LessonAttachment)
class LessonAttachmentAdmin(admin.ModelAdmin):
    list_display = ['file_name', 'lesson', 'mime_type', 'file_size', 'version', 'created_at']

@admin.register(LessonSection)
class LessonSectionAdmin(admin.ModelAdmin):
    list_display = ['title', 'lesson', 'order', 'school']
    list_filter = ['school']

@admin.register(SectionContentBlock)
class SectionContentBlockAdmin(admin.ModelAdmin):
    list_display = ['section', 'block_type', 'order']
    list_filter = ['block_type']

@admin.register(SectionMiniQuiz)
class SectionMiniQuizAdmin(admin.ModelAdmin):
    list_display = ['section', 'quiz', 'passing_score']

@admin.register(StudentSectionProgress)
class StudentSectionProgressAdmin(admin.ModelAdmin):
    list_display = ['student', 'section', 'status', 'best_score', 'attempts', 'completed_at']
    list_filter = ['status', 'school']
