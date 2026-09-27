from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models

ALLOWED_ATTACHMENT_EXTENSIONS = {'.pdf', '.png', '.jpg', '.jpeg', '.gif', '.txt', '.doc', '.docx'}
MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10MB — MVP is text/compressed images/PDFs, not video


class Lesson(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        PUBLISHED = 'PUBLISHED', 'Published'
        ARCHIVED = 'ARCHIVED', 'Archived'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='lessons')
    subject = models.ForeignKey('academics.Subject', on_delete=models.CASCADE, related_name='lessons')
    class_level = models.ForeignKey('academics.ClassLevel', on_delete=models.PROTECT, related_name='lessons')
    topic = models.ForeignKey(
        'academics.Topic', on_delete=models.SET_NULL, null=True, blank=True, related_name='lessons',
    )
    learning_objective = models.ForeignKey(
        'academics.LearningObjective', on_delete=models.SET_NULL, null=True, blank=True, related_name='lessons',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    content = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_lessons',
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def clean(self):
        super().clean()
        if self.subject_id and self.school_id and self.subject.school_id != self.school_id:
            raise ValidationError('A lesson must belong to the same school as its subject.')
        if self.topic_id:
            if self.subject_id and self.topic.subject_id != self.subject_id:
                raise ValidationError('The lesson topic must belong to the lesson subject.')
            if self.class_level_id and self.topic.class_level_id != self.class_level_id:
                raise ValidationError('The lesson topic must belong to the lesson class level.')
        if self.learning_objective_id and self.topic_id and self.learning_objective.topic_id != self.topic_id:
            raise ValidationError('The learning objective must belong to the lesson topic.')

    def __str__(self):
        return f'{self.title} ({self.school.code})'


class LessonAssignment(models.Model):
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='assignments')
    school_class = models.ForeignKey(
        'classes.SchoolClass', on_delete=models.CASCADE, related_name='lesson_assignments',
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='lesson_assignments_made',
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['lesson', 'school_class'], name='unique_lesson_per_class'),
        ]

    def clean(self):
        super().clean()
        if self.lesson_id and self.school_class_id and self.lesson.school_id != self.school_class.school_id:
            raise ValidationError('A lesson can only be assigned to a class in the same school.')

    def __str__(self):
        return f'{self.lesson.title} -> {self.school_class.name}'


def lesson_attachment_upload_path(instance, filename):
    return f'lesson_attachments/{instance.lesson.school_id}/{instance.lesson_id}/{filename}'


class LessonAttachment(models.Model):
    """Stores metadata only — the actual bytes live in the configured file
    storage backend (local disk in dev, swappable to object storage later),
    never as a blob in PostgreSQL. file_size/mime_type/checksum are always
    computed server-side at upload time, never trusted from client input."""

    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to=lesson_attachment_upload_path)
    file_name = models.CharField(max_length=255, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    file_size = models.PositiveIntegerField(default=0)
    checksum = models.CharField(max_length=64, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.file_name} v{self.version} ({self.lesson.title})'


# --- Mastery-based sections -------------------------------------------
#
# A lesson MAY be broken into sequential, gated sections instead of (or
# alongside) its flat `content`/attachments -- fully additive, so a
# lesson with zero sections behaves exactly as before. Each section has
# its own content blocks and, optionally, a mini-quiz that gates access
# to the next section. The mini-quiz deliberately reuses apps.quizzes'
# existing Quiz/Question/QuestionOption/QuizMarkingService rather than a
# parallel schema -- deterministic marking (never AI) comes for free,
# and MCQ/TRUE_FALSE/MATCHING are all already supported.


class LessonSection(models.Model):
    # Denormalized from lesson.school, consistent with the tenant-scoping
    # pattern used throughout (Question, LessonAttachment's parent, ...).
    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='lesson_sections')
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name='sections')
    title = models.CharField(max_length=255)
    order = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['lesson', 'order'], name='unique_section_order_per_lesson'),
        ]
        ordering = ['order']

    def clean(self):
        super().clean()
        if self.lesson_id and self.school_id and self.lesson.school_id != self.school_id:
            raise ValidationError('A section must belong to the same school as its lesson.')

    def __str__(self):
        return f'{self.lesson.title} - Section {self.order}: {self.title}'


class SectionContentBlock(models.Model):
    class BlockType(models.TextChoices):
        HEADING = 'HEADING', 'Heading'
        TEXT = 'TEXT', 'Text'
        IMAGE = 'IMAGE', 'Image'
        KEY_POINT = 'KEY_POINT', 'Key Point'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='section_content_blocks')
    section = models.ForeignKey(LessonSection, on_delete=models.CASCADE, related_name='content_blocks')
    block_type = models.CharField(max_length=10, choices=BlockType.choices)
    # The block's text for HEADING/TEXT/KEY_POINT; the caption/alt text for
    # an IMAGE block (the actual file lives in `image`, an already-uploaded
    # LessonAttachment -- no second file-upload path duplicated here).
    content = models.TextField(blank=True)
    image = models.ForeignKey(
        LessonAttachment, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    order = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def clean(self):
        super().clean()
        if self.section_id and self.school_id and self.section.school_id != self.school_id:
            raise ValidationError('A content block must belong to the same school as its section.')
        if self.block_type == self.BlockType.IMAGE:
            if not self.image_id:
                raise ValidationError('An IMAGE block requires an image attachment.')
            if self.section_id and self.image_id and self.image.lesson_id != self.section.lesson_id:
                raise ValidationError('The image must be an attachment of the same lesson as the section.')
        elif not self.content:
            raise ValidationError(f'A {self.block_type} block requires content text.')

    def __str__(self):
        return f'{self.section.title} - {self.block_type} #{self.order}'


class SectionMiniQuiz(models.Model):
    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='section_mini_quizzes')
    section = models.OneToOneField(LessonSection, on_delete=models.CASCADE, related_name='mini_quiz')
    quiz = models.OneToOneField('quizzes.Quiz', on_delete=models.CASCADE, related_name='section_mini_quiz')
    passing_score = models.PositiveSmallIntegerField(
        default=70, validators=[MaxValueValidator(100)],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.section_id and self.school_id and self.section.school_id != self.school_id:
            raise ValidationError('A mini-quiz must belong to the same school as its section.')
        if self.quiz_id and self.school_id and self.quiz.school_id != self.school_id:
            raise ValidationError('The mini-quiz must belong to the same school as its section.')

    def __str__(self):
        return f'Mini-quiz for {self.section.title} (pass >= {self.passing_score}%)'


class StudentSectionProgress(models.Model):
    class Status(models.TextChoices):
        LOCKED = 'LOCKED', 'Locked'
        AVAILABLE = 'AVAILABLE', 'Available'
        COMPLETED = 'COMPLETED', 'Completed'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='section_progress')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='section_progress',
    )
    section = models.ForeignKey(LessonSection, on_delete=models.CASCADE, related_name='student_progress')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.LOCKED)
    best_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['student', 'section'], name='unique_progress_per_student_section'),
        ]

    def clean(self):
        super().clean()
        if self.student_id and self.school_id and self.student.school_id != self.school_id:
            raise ValidationError('Progress school must match the student school.')
        if self.section_id and self.school_id and self.section.school_id != self.school_id:
            raise ValidationError('Progress school must match the section school.')

    def __str__(self):
        return f'{self.student.username} - {self.section.title} ({self.status})'
