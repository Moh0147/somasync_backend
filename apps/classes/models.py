import string

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.crypto import get_random_string

def generate_class_code():
    return get_random_string(8, allowed_chars=string.ascii_uppercase + string.digits)

class SchoolClass(models.Model):
    """A concrete class/stream at a school for a given academic year, e.g.
    'Form II Blue' for 2026. `class_code` is a random, globally-unique
    token students use to self-join — never a predictable, human-chosen
    string, and never reused across schools (so a code alone can't be
    mistaken for one belonging to a different school)."""

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='classes')
    name = models.CharField(max_length=100)
    class_level = models.ForeignKey(
        'academics.ClassLevel', on_delete=models.PROTECT, related_name='school_classes',
    )
    academic_year = models.PositiveIntegerField()
    class_code = models.CharField(max_length=12, unique=True, editable=False, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['school', 'name', 'academic_year'], name='unique_class_name_per_school_year',
            ),
        ]
        ordering = ['-academic_year', 'class_level__order', 'name']

    def save(self, *args, **kwargs):
        if not self.class_code:
            code = generate_class_code()
            while SchoolClass.objects.filter(class_code=code).exists():
                code = generate_class_code()
            self.class_code = code
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.name} ({self.academic_year}) - {self.school.code}'

class TeacherClassMembership(models.Model):
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name='teacher_memberships')
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='teaching_classes')
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['school_class', 'teacher'], name='unique_teacher_per_class'),
        ]

    def clean(self):
        super().clean()
        User = get_user_model()
        if self.teacher_id and self.teacher.role != User.Role.TEACHER:
            raise ValidationError('Only a user with role=TEACHER can be assigned to a class.')
        if self.school_class_id and self.teacher_id and self.teacher.school_id != self.school_class.school_id:
            raise ValidationError('The teacher must belong to the same school as the class.')

    def __str__(self):
        return f'{self.teacher.username} -> {self.school_class.name}'

class StudentClassMembership(models.Model):
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name='student_memberships')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='enrolled_classes')
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['school_class', 'student'], name='unique_student_per_class'),
        ]

    def clean(self):
        super().clean()
        User = get_user_model()
        if self.student_id and self.student.role != User.Role.STUDENT:
            raise ValidationError('Only a user with role=STUDENT can be enrolled in a class.')
        if self.school_class_id and self.student_id and self.student.school_id != self.school_class.school_id:
            raise ValidationError('The student must belong to the same school as the class.')

    def __str__(self):
        return f'{self.student.username} -> {self.school_class.name}'
