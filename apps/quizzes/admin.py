from django.contrib import admin

from .models import (
    MatchingPair, Question, QuestionOption, Quiz, QuizAssignment, StudentQuizAnswer, StudentQuizAttempt,
)

class QuestionOptionInline(admin.TabularInline):
    model = QuestionOption
    extra = 0

class MatchingPairInline(admin.TabularInline):
    model = MatchingPair
    extra = 0

@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ['title', 'school', 'subject', 'class_level', 'status', 'created_by', 'published_at']
    list_filter = ['school', 'status', 'subject', 'class_level']
    search_fields = ['title']

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ['text', 'quiz', 'question_type', 'marks', 'order']
    list_filter = ['question_type', 'quiz__school']
    inlines = [QuestionOptionInline, MatchingPairInline]

@admin.register(QuizAssignment)
class QuizAssignmentAdmin(admin.ModelAdmin):
    list_display = ['quiz', 'school_class', 'assigned_by', 'assigned_at']

class StudentQuizAnswerInline(admin.TabularInline):
    model = StudentQuizAnswer
    extra = 0

@admin.register(StudentQuizAttempt)
class StudentQuizAttemptAdmin(admin.ModelAdmin):
    list_display = ['student', 'quiz', 'status', 'client_attempt_id', 'score', 'started_at', 'submitted_at']
    list_filter = ['school', 'status', 'quiz']
    search_fields = ['client_attempt_id', 'student__username']
    inlines = [StudentQuizAnswerInline]
