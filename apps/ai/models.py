from django.conf import settings
from django.db import models


class AIExplanationRequest(models.Model):
    """One student's request for an AI-generated explanation of a specific
    wrong quiz answer. `attempts`/`status` support a queue-like lifecycle
    even though generation runs synchronously today — the shape is ready
    for an async worker later without a schema change (see Phase 9's
    SYNC_PENDING for the same pattern)."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PROCESSING = 'PROCESSING', 'Processing'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='ai_explanation_requests')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ai_explanation_requests',
    )
    student_answer = models.ForeignKey(
        'quizzes.StudentQuizAnswer', on_delete=models.CASCADE, related_name='explanation_requests',
    )
    context = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    explanation = models.TextField(blank=True)
    correct_concept = models.TextField(blank=True)
    concise_feedback = models.TextField(blank=True)
    error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.student.username} - answer {self.student_answer_id} ({self.status})'


class MistakeExplanationCache(models.Model):
    """A reusable explanation for "Question X, this specific wrong
    answer" — many students commonly make the same mistake on the same
    question, so the first AI-generated explanation for a given
    (question, wrong_answer) pair is cached and reused, saving an LLM
    call and letting the frontend serve it even while offline."""

    question = models.ForeignKey(
        'quizzes.Question', on_delete=models.CASCADE, related_name='mistake_explanation_cache_entries',
    )
    wrong_answer_signature = models.CharField(max_length=80)
    explanation = models.TextField()
    correct_concept = models.TextField()
    concise_feedback = models.TextField()
    hit_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['question', 'wrong_answer_signature'], name='unique_cache_per_question_wrong_answer',
            ),
        ]

    def __str__(self):
        return f'Cache: Q{self.question_id} / {self.wrong_answer_signature}'
