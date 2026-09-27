from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.core.exceptions import ValidationError
from django.db import models


class UserManager(DjangoUserManager):
    """Django's default create_superuser() has no idea about `role`/
    `admin_scope`/`school` -- it would leave role='' (no default), which
    violates the school_required_unless_system_admin CHECK constraint on
    every save. A Django superuser IS this project's system admin (the
    only role that legitimately spans every school), so createsuperuser
    should just produce one directly rather than failing or requiring the
    operator to know these three fields exist."""

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault('role', User.Role.ADMIN)
        extra_fields.setdefault('admin_scope', User.AdminScope.SYSTEM)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """SomaSync's custom user. Exactly three roles exist — ADMIN, TEACHER,
    STUDENT — never separate system-admin/school-admin roles. An ADMIN's
    scope (SYSTEM vs SCHOOL) is tracked separately via `admin_scope`.

    `school` is the tenant boundary: NULL for a system admin (who spans all
    schools), required for everyone else (school admin, teacher, student).
    """

    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        TEACHER = 'TEACHER', 'Teacher'
        STUDENT = 'STUDENT', 'Student'

    class AdminScope(models.TextChoices):
        SYSTEM = 'SYSTEM', 'System'
        SCHOOL = 'SCHOOL', 'School'

    role = models.CharField(max_length=10, choices=Role.choices)
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
                ),
                name='school_required_unless_system_admin',
            ),
        ]

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
        if self.role in (self.Role.TEACHER, self.Role.STUDENT) and not self.school_id:
            raise ValidationError(f'{self.get_role_display()}s must belong to a school.')

    def __str__(self):
        return f'{self.username} ({self.role})'
