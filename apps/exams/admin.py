from django.contrib import admin

from .models import (
    Exam, ExamCandidate, ExamQuestion, ExamScript, MarkingSchemeItem, StudentExamAnswer, StudentExamAttempt,
)


class MarkingSchemeItemInline(admin.TabularInline):
    model = MarkingSchemeItem
    extra = 0


@admin.register(Exam)
class ExamAdmin(admin.ModelAdmin):
    list_display = ['title', 'school', 'subject', 'class_level', 'academic_year', 'status']
    list_filter = ['school', 'status', 'academic_year']
    search_fields = ['title']


@admin.register(ExamQuestion)
class ExamQuestionAdmin(admin.ModelAdmin):
    list_display = ['exam', 'question_number', 'question_type', 'max_marks']
    list_filter = ['exam__school', 'question_type']
    inlines = [MarkingSchemeItemInline]


@admin.register(ExamCandidate)
class ExamCandidateAdmin(admin.ModelAdmin):
    list_display = ['candidate_number', 'exam', 'student']
    search_fields = ['candidate_number', 'student__username']


@admin.register(ExamScript)
class ExamScriptAdmin(admin.ModelAdmin):
    list_display = ['candidate', 'exam', 'version', 'status', 'file_size', 'uploaded_at']
    list_filter = ['school', 'status']
    search_fields = ['candidate__candidate_number', 'file_name']


class StudentExamAnswerInline(admin.TabularInline):
    model = StudentExamAnswer
    extra = 0


@admin.register(StudentExamAttempt)
class StudentExamAttemptAdmin(admin.ModelAdmin):
    list_display = ['candidate', 'exam', 'score', 'total_marks', 'created_at']
    list_filter = ['school']
    inlines = [StudentExamAnswerInline]
