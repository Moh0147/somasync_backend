from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator
from django.db import models
from django.utils import timezone

class LessonProgress(models.Model):
    """A student's progress through a lesson. `last_accessed_at` is
    client-settable (not auto_now) — offline-first: a student may report
    access that happened long before this row is actually synced/written,
    so it's the client's own timestamp, while `updated_at` is the server's
    own record of when this row was last written."""

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='lesson_progress')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='lesson_progress',
    )
    lesson = models.ForeignKey('lessons.Lesson', on_delete=models.CASCADE, related_name='progress_records')
    progress_percentage = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    completed = models.BooleanField(default=False)
    last_position = models.CharField(max_length=255, blank=True)
    last_accessed_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['student', 'lesson'], name='unique_progress_per_student_lesson'),
        ]
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['student', 'updated_at'], name='lessonprog_stu_upd_idx'),
        ]

    def clean(self):
        super().clean()
        if self.student_id and self.lesson_id and self.student.school_id != self.lesson.school_id:
            raise ValidationError('A student can only track progress on a lesson from their own school.')
        if self.school_id and self.lesson_id and self.school_id != self.lesson.school_id:
            raise ValidationError('Progress school must match the lesson school.')

    def __str__(self):
        return f'{self.student.username} - {self.lesson.title} ({self.progress_percentage}%)'
