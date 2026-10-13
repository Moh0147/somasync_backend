from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.db import models

class UserManager(DjangoUserManager):

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault('role', User.Role.ADMIN)
        extra_fields.setdefault('admin_scope', User.AdminScope.SYSTEM)
        return super().create_superuser(username, email, password, **extra_fields)

class User(AbstractUser):

    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        TEACHER = 'TEACHER', 'Teacher'
        STUDENT = 'STUDENT', 'Student'
        EXAM_MARKING_MANAGER = 'EXAM_MARKING_MANAGER', 'Exam Marking Manager'
        CHIEF_EXAMINER = 'CHIEF_EXAMINER', 'Chief Examiner'
        EXAMINER = 'EXAMINER', 'Examiner'
        CHECKER = 'CHECKER', 'Checker'
        DATA_ENTRY_CLERK = 'DATA_ENTRY_CLERK', 'Data Entry Clerk'
        IT_SUPPORT = 'IT_SUPPORT', 'IT Support'

    class AdminScope(models.TextChoices):
        SYSTEM = 'SYSTEM', 'System'
        SCHOOL = 'SCHOOL', 'School'

    _SCHOOL_REQUIRED_ROLES = {
        'TEACHER', 'STUDENT',
        'EXAMINER', 'CHECKER', 'DATA_ENTRY_CLERK', 'IT_SUPPORT',
    }
    _SCHOOL_OPTIONAL_MARKING_ROLES = {'EXAM_MARKING_MANAGER', 'CHIEF_EXAMINER'}

    role = models.CharField(max_length=20, choices=Role.choices)
    secondary_role = models.CharField(
        max_length=20, choices=Role.choices, null=True, blank=True,
        help_text='Optional second role for teachers who also serve as examiners or checkers.',
    )
    admin_scope = models.CharField(
        max_length=10, choices=AdminScope.choices, null=True, blank=True,
        help_text='Required and only meaningful when role=ADMIN.',
    )
    phone_number = models.CharField(max_length=20, blank=True)
    school = models.ForeignKey(
        'schools.School', null=True, blank=True, on_delete=models.PROTECT, related_name='users',
        help_text='NULL only for a system admin. Required for school admin, teacher, and student.',
    )

    objects = UserManager()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(role='ADMIN', admin_scope__isnull=False)
                    | (~models.Q(role='ADMIN') & models.Q(admin_scope__isnull=True))
                ),
                name='admin_scope_required_iff_admin_role',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(role='ADMIN', admin_scope='SYSTEM', school__isnull=True)
                    | models.Q(role='ADMIN', admin_scope='SCHOOL', school__isnull=False)
                    | models.Q(role='TEACHER', school__isnull=False)
                    | models.Q(role='STUDENT', school__isnull=False)
                    | models.Q(role='EXAM_MARKING_MANAGER')
                    | models.Q(role='CHIEF_EXAMINER')
                    | models.Q(role='EXAMINER', school__isnull=False)
                    | models.Q(role='CHECKER', school__isnull=False)
                    | models.Q(role='DATA_ENTRY_CLERK', school__isnull=False)
                    | models.Q(role='IT_SUPPORT', school__isnull=False)
                ),
                name='school_required_unless_system_admin',
            ),
        ]

    _SECONDARY_ROLE_ALLOWED_PRIMARY = {'TEACHER'}
    _SECONDARY_ROLE_ALLOWED_VALUES = {'EXAMINER', 'CHECKER'}

    def clean(self):
        super().clean()
        if self.role == self.Role.ADMIN and not self.admin_scope:
            raise ValidationError('admin_scope is required when role is ADMIN.')
        if self.role != self.Role.ADMIN and self.admin_scope:
            raise ValidationError('admin_scope must be empty unless role is ADMIN.')

        if self.role == self.Role.ADMIN and self.admin_scope == self.AdminScope.SYSTEM and self.school_id:
            raise ValidationError('A system admin must not belong to a school.')
        if self.role == self.Role.ADMIN and self.admin_scope == self.AdminScope.SCHOOL and not self.school_id:
            raise ValidationError('A school admin must belong to a school.')
        if self.role in self._SCHOOL_REQUIRED_ROLES and not self.school_id:
            raise ValidationError(f'{self.get_role_display()}s must belong to a school.')
        if self.role in self._SCHOOL_OPTIONAL_MARKING_ROLES and self.admin_scope:
            raise ValidationError('admin_scope must be empty for marking management roles.')

        if self.secondary_role:
            if self.role not in self._SECONDARY_ROLE_ALLOWED_PRIMARY:
                raise ValidationError('secondary_role is only allowed when the primary role is TEACHER.')
            if self.secondary_role not in self._SECONDARY_ROLE_ALLOWED_VALUES:
                raise ValidationError('secondary_role must be EXAMINER or CHECKER.')

    def __str__(self):
        return f'{self.username} ({self.role})'
