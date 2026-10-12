from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

class Exam(models.Model):
    """A physical school exam SomaSync helps manage — never a replacement
    for the paper sitting itself. Authoring-only in this phase; there is
    no student-facing "take this exam" flow the way Quiz has one."""

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        PUBLISHED = 'PUBLISHED', 'Published'
        CLOSED = 'CLOSED', 'Closed'
        MARKING = 'MARKING', 'Marking'
        REVIEW = 'REVIEW', 'Review'
        FINALIZED = 'FINALIZED', 'Finalized'
        RESULTS_PUBLISHED = 'RESULTS_PUBLISHED', 'Results Published'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='exams')
    subject = models.ForeignKey('academics.Subject', on_delete=models.CASCADE, related_name='exams')
    class_level = models.ForeignKey('academics.ClassLevel', on_delete=models.PROTECT, related_name='exams')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    academic_year = models.PositiveIntegerField()
    exam_date = models.DateField(null=True, blank=True)
    total_marks = models.PositiveIntegerField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    section_settings = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_exams')
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-academic_year', '-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['school', 'title', 'academic_year'], name='unique_exam_title_per_school_year',
            ),
        ]

    def clean(self):
        super().clean()
        if self.subject_id and self.school_id and self.subject.school_id != self.school_id:
            raise ValidationError('An exam must belong to the same school as its subject.')

    def __str__(self):
        return f'{self.title} ({self.academic_year}) - {self.school.code}'

class MarkingSchemeDocument(models.Model):
    """Original uploaded marking scheme retained for teacher audit."""
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='marking_scheme_documents')
    file = models.FileField(upload_to='exam_marking_schemes/%Y/%m/')
    file_name = models.CharField(max_length=255)
    extracted_text = models.TextField(blank=True)
    source_type = models.CharField(max_length=20, default='MARKING_SCHEME')
    status = models.CharField(max_length=20, default='REVIEW_REQUIRED')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

class ExamQuestion(models.Model):
    class Type(models.TextChoices):
        OBJECTIVE = 'OBJECTIVE', 'Objective'
        SUBJECTIVE = 'SUBJECTIVE', 'Subjective'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='exam_questions')
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='questions')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='authored_exam_questions',
    )
    section = models.CharField(max_length=50, blank=True)
    question_number = models.CharField(max_length=10)
    question_text = models.TextField()
    question_type = models.CharField(max_length=10, choices=Type.choices)
    max_marks = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0.01)])
    learning_objective = models.ForeignKey(
        'academics.LearningObjective', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='exam_questions',
    )
    correct_answer = models.CharField(max_length=255, blank=True)
    is_compulsory = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']
        constraints = [
            models.UniqueConstraint(fields=['exam', 'question_number'], name='unique_question_number_per_exam'),
        ]

    def clean(self):
        super().clean()
        if self.exam_id and self.school_id and self.exam.school_id != self.school_id:
            raise ValidationError('An exam question must belong to the same school as its exam.')
        if self.learning_objective_id and self.exam_id:
            if self.learning_objective.topic.subject_id != self.exam.subject_id:
                raise ValidationError('The learning objective must belong to the exam subject.')
        if self.question_type == self.Type.OBJECTIVE and not self.correct_answer:
            raise ValidationError('An OBJECTIVE question requires a correct_answer for deterministic marking.')

    def __str__(self):
        return f'{self.exam.title} Q{self.question_number}'

class MarkingSchemeItem(models.Model):
    """One criterion within a question's marking scheme. The sum of an
    exam question's marking scheme items must never exceed its max_marks
    — enforced at creation time (ExamQuestionViewSet.marking_scheme), not
    just at some later finalization step."""

    class MarksSource(models.TextChoices):
        CONFIRMED = 'confirmed', 'Confirmed from official scheme'
        INFERRED  = 'inferred',  'Inferred/estimated (e.g. from CIRA)'

    exam_question = models.ForeignKey(ExamQuestion, on_delete=models.CASCADE, related_name='marking_scheme_items')
    criterion = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    marks = models.DecimalField(max_digits=6, decimal_places=2, validators=[MinValueValidator(0.01)])
    order = models.PositiveIntegerField(default=0)
    marks_source = models.CharField(
        max_length=10, choices=MarksSource.choices, default=MarksSource.CONFIRMED,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.criterion} ({self.marks} marks)'

class ExamCandidate(models.Model):
    """Links a student to an exam via a candidate number, unique per exam —
    the identity mechanism scripts are matched against later."""

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='candidates')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='exam_candidacies')
    candidate_number = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['exam', 'candidate_number'], name='unique_candidate_number_per_exam'),
            models.UniqueConstraint(fields=['exam', 'student'], name='unique_student_per_exam'),
        ]
        ordering = ['candidate_number']

    def clean(self):
        super().clean()
        if self.exam_id and self.student_id and self.exam.school_id != self.student.school_id:
            raise ValidationError('A candidate must belong to the same school as the exam.')

    def __str__(self):
        return f'{self.candidate_number} - {self.student.username}'

ALLOWED_SCRIPT_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg'}
MAX_SCRIPT_SIZE_BYTES = 25 * 1024 * 1024

def exam_script_upload_path(instance, filename):
    return f'exam_scripts/{instance.school_id}/{instance.exam_id}/{instance.candidate_id}/{filename}'

def exam_script_page_upload_path(instance, filename):
    return (
        f'exam_script_pages/{instance.script.school_id}/'
        f'{instance.script.exam_id}/{instance.script.candidate_id}/{filename}'
    )

class ExamScript(models.Model):
    """A scanned exam script upload. Stores metadata only — the file
    itself lives in the configured storage backend, never as bytes in
    PostgreSQL (same pattern as LessonAttachment, Phase 6).

    Identity is established via `candidate` (matched by candidate_number,
    never handwriting/OCR) — `student`/`school` are then derived from the
    candidate and re-validated for consistency, never taken independently
    from client input.

    Re-uploading for the same candidate creates a NEW row with an
    incremented `version` rather than overwriting in place — preserves an
    audit trail, which matters for exam integrity."""

    class OCRStatus(models.TextChoices):
        NOT_STARTED = 'NOT_STARTED', 'Not started'
        PROCESSING = 'PROCESSING', 'Processing'
        READY = 'READY', 'Ready for review'
        FAILED = 'FAILED', 'Failed'

    class Status(models.TextChoices):
        UPLOADED = 'UPLOADED', 'Uploaded'
        PROCESSING = 'PROCESSING', 'Processing'
        MARKED = 'MARKED', 'Marked'
        TEACHER_REVIEW = 'TEACHER_REVIEW', 'Teacher Review'
        FINALIZED = 'FINALIZED', 'Finalized'
        PUBLISHED = 'PUBLISHED', 'Published'
        FAILED = 'FAILED', 'Failed'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='exam_scripts')
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='scripts')
    candidate = models.ForeignKey(ExamCandidate, on_delete=models.CASCADE, related_name='scripts')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='exam_scripts')
    file = models.FileField(upload_to=exam_script_upload_path)
    file_name = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.UPLOADED)
    marking_error = models.TextField(blank=True)
    ocr_status = models.CharField(max_length=15, choices=OCRStatus.choices, default=OCRStatus.NOT_STARTED)
    extracted_text = models.TextField(blank=True)
    ocr_error = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='uploaded_exam_scripts',
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-version', '-uploaded_at']

    def clean(self):
        super().clean()
        if self.candidate_id and self.exam_id and self.candidate.exam_id != self.exam_id:
            raise ValidationError('This candidate is not registered for this exam.')
        if self.candidate_id and self.student_id and self.candidate.student_id != self.student_id:
            raise ValidationError("The script's student must match the candidate's registered student.")
        if self.exam_id and self.school_id and self.exam.school_id != self.school_id:
            raise ValidationError('Script school must match the exam school.')
        if self.student_id and self.school_id and self.student.school_id != self.school_id:
            raise ValidationError("The script's student must belong to the script's school.")

    def __str__(self):
        return f'{self.candidate.candidate_number} v{self.version} ({self.status})'

class ExamScriptPage(models.Model):
    """One rendered page from an ExamScript file.

    Created automatically when a script is uploaded. Each page is stored as
    a PNG image so the teacher can view the original handwriting (including
    diagrams) alongside the OCR-extracted text during marking review."""

    script = models.ForeignKey(ExamScript, on_delete=models.CASCADE, related_name='pages')
    page_number = models.PositiveIntegerField()
    image = models.ImageField(upload_to=exam_script_page_upload_path)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['page_number']
        constraints = [
            models.UniqueConstraint(fields=['script', 'page_number'], name='unique_page_number_per_script'),
        ]

    def __str__(self):
        return f'{self.script} — page {self.page_number}'

class ExamScriptAssignment(models.Model):
    """Assigns an EXAMINER or CHECKER to a specific script.

    Created by EXAM_MARKING_MANAGER or CHIEF_EXAMINER via the allocations
    API (Phase 4). Present from Phase 2 so `_can_manage` / `_can_mark`
    can enforce per-examiner script scoping without script ID guessing."""

    class Role(models.TextChoices):
        EXAMINER = 'EXAMINER', 'Examiner'
        CHECKER = 'CHECKER', 'Checker'

    script = models.ForeignKey(ExamScript, on_delete=models.CASCADE, related_name='assignments')
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='script_assignments',
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='script_assignments_made',
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['script', 'assignee', 'role'], name='unique_script_assignment_per_role',
            ),
        ]
        ordering = ['assigned_at']

    def clean(self):
        super().clean()
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if self.assignee_id and self.assignee.role not in ('EXAMINER', 'CHECKER'):
            raise ValidationError('Only EXAMINER or CHECKER users can be assigned to scripts.')
        if self.script_id and self.assignee_id and self.script.school_id != self.assignee.school_id:
            raise ValidationError('Assignee must belong to the same school as the script.')

    def __str__(self):
        return f'{self.assignee.username} ({self.role}) → {self.script}'

class StudentExamAttempt(models.Model):
    """One per (exam, candidate) — enforced via `candidate` being a
    OneToOneField, since ExamCandidate already uniquely pairs (exam,
    student). `current_script` points at the latest ExamScript version;
    older versions remain in the database for audit purposes.

    score/total_marks/percentage are populated by Phase 16/17's marking
    engine, not by anything in this phase — same deferred-population
    pattern as StudentQuizAttempt (Phase 8 added the fields, Phase 9
    populated them)."""

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='exam_attempts')
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='attempts')
    candidate = models.OneToOneField(ExamCandidate, on_delete=models.CASCADE, related_name='attempt')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='exam_attempts')
    current_script = models.ForeignKey(
        ExamScript, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    total_marks = models.PositiveIntegerField(null=True, blank=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        if self.candidate_id and self.exam_id and self.candidate.exam_id != self.exam_id:
            raise ValidationError('This candidate is not registered for this exam.')

    def __str__(self):
        return f'{self.candidate.candidate_number} - {self.exam.title}'

class StudentExamAnswer(models.Model):
    """One row per (attempt, exam_question), auto-created as an empty
    placeholder when a script is uploaded (Phase 15), then populated by
    marking (Phase 16/17).

    `ai_marks_awarded`/`ai_feedback` are the AI's SUGGESTION for a
    SUBJECTIVE question (Phase 17 — never populated by this phase) and
    are stored entirely separately from `teacher_final_marks`/
    `teacher_comment`, which is the authoritative, human-confirmed mark.
    For an OBJECTIVE question, ExamMarkingService (this phase) sets
    `teacher_final_marks` directly and deterministically — there's no
    ambiguity to review, so no AI suggestion is ever involved.
    `recorded_answer` is filled in manually (never OCR/handwriting
    recognition) so deterministic marking has something to compare
    against `exam_question.correct_answer`."""

    attempt = models.ForeignKey(StudentExamAttempt, on_delete=models.CASCADE, related_name='answers')
    exam_question = models.ForeignKey(ExamQuestion, on_delete=models.CASCADE, related_name='student_answers')
    recorded_answer = models.TextField(blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    ai_marks_awarded = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    ai_feedback = models.TextField(blank=True)
    ocr_confidence = models.CharField(max_length=10, blank=True)
    teacher_final_marks = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    teacher_comment = models.TextField(blank=True)
    marked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['attempt', 'exam_question'], name='one_answer_per_question_per_exam_attempt',
            ),
        ]
        ordering = ['exam_question__order', 'id']

    def clean(self):
        super().clean()
        if self.attempt_id and self.exam_question_id and self.exam_question.exam_id != self.attempt.exam_id:
            raise ValidationError("This question does not belong to the attempt's exam.")

        max_marks = self.exam_question.max_marks if self.exam_question_id else None
        if max_marks is not None:
            if self.ai_marks_awarded is not None and not (0 <= self.ai_marks_awarded <= max_marks):
                raise ValidationError(f'ai_marks_awarded must be between 0 and {max_marks}.')
            if self.teacher_final_marks is not None and not (0 <= self.teacher_final_marks <= max_marks):
                raise ValidationError(f'teacher_final_marks must be between 0 and {max_marks}.')

    def __str__(self):
        return f'{self.attempt} - Q{self.exam_question.question_number}'

class MarkingAuditLog(models.Model):
    """Append-only record of every state-changing action in the marking
    domain. FKs use SET_NULL so the trail survives deletion of its
    subjects. `actor_username`/`actor_role` are snapshotted at write time
    so the log stays readable after account changes."""

    class Action(models.TextChoices):
        SCRIPT_SUBMITTED = 'SCRIPT_SUBMITTED', 'Submitted for review'
        SCRIPT_FINALIZED = 'SCRIPT_FINALIZED', 'Finalized'
        SCRIPT_AI_MARKED = 'SCRIPT_AI_MARKED', 'AI marked'
        SCRIPT_OCR_RAN = 'SCRIPT_OCR_RAN', 'OCR ran'
        SCRIPT_RESET = 'SCRIPT_RESET', 'Emergency reset'
        ANSWER_MARKED = 'ANSWER_MARKED', 'Answer marked'
        SCRIPT_ASSIGNED = 'SCRIPT_ASSIGNED', 'Script assigned'
        SCRIPT_UNASSIGNED = 'SCRIPT_UNASSIGNED', 'Assignment removed'

    exam = models.ForeignKey(
        Exam, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs',
    )
    script = models.ForeignKey(
        ExamScript, on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_logs',
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        related_name='marking_audit_logs',
    )
    actor_username = models.CharField(max_length=150)
    actor_role = models.CharField(max_length=50)
    action = models.CharField(max_length=20, choices=Action.choices)
    detail = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['exam', 'created_at']),
            models.Index(fields=['actor', 'created_at']),
            models.Index(fields=['action', 'created_at']),
        ]

    def __str__(self):
        return f'{self.actor_username} — {self.action} at {self.created_at:%Y-%m-%d %H:%M}'

