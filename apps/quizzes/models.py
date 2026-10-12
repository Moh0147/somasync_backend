from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

class Quiz(models.Model):
    """A normal quiz: MCQ / True-False / Matching only, marked
    deterministically (see QuizMarkingService, Phase 9) — AI is never used
    for normal quiz marking."""

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        PUBLISHED = 'PUBLISHED', 'Published'
        ARCHIVED = 'ARCHIVED', 'Archived'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='quizzes')
    subject = models.ForeignKey('academics.Subject', on_delete=models.CASCADE, related_name='quizzes')
    class_level = models.ForeignKey('academics.ClassLevel', on_delete=models.PROTECT, related_name='quizzes')
    topic = models.ForeignKey(
        'academics.Topic', on_delete=models.SET_NULL, null=True, blank=True, related_name='quizzes',
    )
    learning_objective = models.ForeignKey(
        'academics.LearningObjective', on_delete=models.SET_NULL, null=True, blank=True, related_name='quizzes',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_quizzes',
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        if self.subject_id and self.school_id and self.subject.school_id != self.school_id:
            raise ValidationError('A quiz must belong to the same school as its subject.')
        if self.topic_id:
            if self.subject_id and self.topic.subject_id != self.subject_id:
                raise ValidationError('The quiz topic must belong to the quiz subject.')
            if self.class_level_id and self.topic.class_level_id != self.class_level_id:
                raise ValidationError('The quiz topic must belong to the quiz class level.')
        if self.learning_objective_id and self.topic_id and self.learning_objective.topic_id != self.topic_id:
            raise ValidationError('The learning objective must belong to the quiz topic.')

    def __str__(self):
        return f'{self.title} ({self.school.code})'

class Question(models.Model):
    class Type(models.TextChoices):
        MCQ = 'MCQ', 'Multiple Choice'
        TRUE_FALSE = 'TRUE_FALSE', 'True/False'
        MATCHING = 'MATCHING', 'Matching'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='questions')
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='questions')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='authored_questions',
    )
    question_type = models.CharField(max_length=12, choices=Type.choices)
    text = models.TextField()
    marks = models.PositiveIntegerField(default=1)
    order = models.PositiveIntegerField(default=0)
    explanation = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']

    def clean(self):
        super().clean()
        if self.quiz_id and self.school_id and self.quiz.school_id != self.school_id:
            raise ValidationError('A question must belong to the same school as its quiz.')

    def __str__(self):
        return f'[{self.question_type}] {self.text[:50]}'

class QuestionOption(models.Model):
    """Used by both MCQ (>=2 options) and TRUE_FALSE (exactly 2) — exactly
    one option must be marked correct. Structural completeness is enforced
    at quiz-publish time, not at option-creation time, so a teacher can
    build a question incrementally."""

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='options')
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.text} ({"correct" if self.is_correct else "incorrect"})'

class MatchingPair(models.Model):
    """One correct left/right match for a MATCHING question. The stored
    pairing IS the answer key — a question needs at least 2 pairs to be a
    meaningful matching exercise."""

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='pairs')
    left_text = models.CharField(max_length=255)
    right_text = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.left_text} -> {self.right_text}'

class QuizAssignment(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='assignments')
    school_class = models.ForeignKey(
        'classes.SchoolClass', on_delete=models.CASCADE, related_name='quiz_assignments',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='quiz_assignments_made',
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['quiz', 'school_class'], name='unique_quiz_per_class'),
        ]

    def clean(self):
        super().clean()
        if self.quiz_id and self.school_class_id and self.quiz.school_id != self.school_class.school_id:
            raise ValidationError('A quiz can only be assigned to a class in the same school.')

    def __str__(self):
        return f'{self.quiz.title} -> {self.school_class.name}'

class StudentQuizAttempt(models.Model):
    """A student's attempt at a quiz, designed to be created and filled in
    entirely offline and synced later. `client_attempt_id` is generated by
    the device (never the server) and is the idempotency key: syncing the
    same attempt twice must resolve to the same row, never a duplicate.

    Statuses:
      IN_PROGRESS  — created (live `start`, or `sync` with submitted=false),
                     still being answered.
      SUBMITTED    — finalized via the live `submit` action.
      SYNC_PENDING — a `sync` push is being processed; a very short-lived
                     state today (synchronous), but modeled as its own
                     status since real offline sync can take a while to
                     resolve (retries, conflicts) once this becomes async.
      SYNCED       — a `sync` push with submitted=true completed and
                     validated successfully.
      FAILED       — a `sync` push's answers failed validation; the
                     attempt row is kept (with `sync_error`) so the client
                     can see what went wrong and retry.

    score/total_marks/percentage are populated by Phase 9's
    QuizMarkingService, not by anything in this phase.
    """

    class Status(models.TextChoices):
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        SYNC_PENDING = 'SYNC_PENDING', 'Sync Pending'
        SYNCED = 'SYNCED', 'Synced'
        FAILED = 'FAILED', 'Failed'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='quiz_attempts')
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='attempts')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='quiz_attempts',
    )
    client_attempt_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.IN_PROGRESS)
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    total_marks = models.PositiveIntegerField(null=True, blank=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    sync_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['student', 'updated_at'], name='quizattempt_stu_upd_idx'),
        ]

    def clean(self):
        super().clean()
        if self.student_id and self.quiz_id and self.student.school_id != self.quiz.school_id:
            raise ValidationError('A student can only attempt a quiz from their own school.')
        if self.school_id and self.quiz_id and self.school_id != self.quiz.school_id:
            raise ValidationError('Attempt school must match the quiz school.')

    def __str__(self):
        return f'{self.student.username} -> {self.quiz.title} ({self.status})'

class StudentQuizAnswer(models.Model):
    """One row per (attempt, question) — re-answering the same question
    (whether live or via a later sync) overwrites the previous answer
    rather than creating a second row. `client_answer_id` is the
    device-generated idempotency key for this specific answer event."""

    attempt = models.ForeignKey(StudentQuizAttempt, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='student_answers')
    client_answer_id = models.CharField(max_length=64)
    selected_option = models.ForeignKey(
        QuestionOption, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    matching_response = models.JSONField(default=dict, blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    marks_awarded = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    answered_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['attempt', 'question'], name='one_answer_per_question_per_attempt'),
        ]

    def clean(self):
        super().clean()
        if self.attempt_id and self.question_id and self.question.quiz_id != self.attempt.quiz_id:
            raise ValidationError("This question does not belong to the attempt's quiz.")
        if self.selected_option_id and self.question_id and self.selected_option.question_id != self.question_id:
            raise ValidationError('This option does not belong to the selected question.')

    def __str__(self):
        return f'{self.attempt} - Q{self.question_id}'
