from rest_framework.permissions import BasePermission

ROLE_ADMIN = 'ADMIN'
ROLE_TEACHER = 'TEACHER'
ROLE_STUDENT = 'STUDENT'

SCOPE_SYSTEM = 'SYSTEM'
SCOPE_SCHOOL = 'SCHOOL'


def _authenticated(request):
    return bool(request.user and request.user.is_authenticated)


def is_system_admin(user):
    """True only for a SYSTEM-scoped admin, who is the sole role allowed to
    span every school (tenant)."""
    return bool(
        user and user.is_authenticated
        and user.role == ROLE_ADMIN and user.admin_scope == SCOPE_SYSTEM
    )


class IsAdmin(BasePermission):
    """Any ADMIN, regardless of scope (SYSTEM or SCHOOL)."""

    def has_permission(self, request, view):
        return _authenticated(request) and request.user.role == ROLE_ADMIN


class IsSystemAdmin(BasePermission):
    def has_permission(self, request, view):
        return is_system_admin(request.user)


class IsSchoolAdmin(BasePermission):
    def has_permission(self, request, view):
        return (
            _authenticated(request)
            and request.user.role == ROLE_ADMIN
            and request.user.admin_scope == SCOPE_SCHOOL
        )


class IsTeacher(BasePermission):
    def has_permission(self, request, view):
        return _authenticated(request) and request.user.role == ROLE_TEACHER


class IsStudent(BasePermission):
    def has_permission(self, request, view):
        return _authenticated(request) and request.user.role == ROLE_STUDENT


class IsOwnSchool(BasePermission):
    """Object-level permission for the School model itself. A SYSTEM admin
    may access any school; everyone else only the school they belong to.
    Compares against request.user.school_id — a client-supplied school_id
    is never consulted here."""

    def has_object_permission(self, request, view, obj):
        if not _authenticated(request):
            return False
        if is_system_admin(request.user):
            return True
        return obj.id == request.user.school_id


class IsTenantObjectOwner(BasePermission):
    """Object-level permission for models with a `school` FK (lessons,
    classes, quizzes, etc. in later phases). A SYSTEM admin may access any
    row; everyone else only rows belonging to their own school."""

    def has_object_permission(self, request, view, obj):
        if not _authenticated(request):
            return False
        if is_system_admin(request.user):
            return True
        return getattr(obj, 'school_id', None) == request.user.school_id


class IsOwnerOrSchoolAdmin(BasePermission):
    """Object-level permission for content a specific user authors within
    their school (lessons, and likely quizzes/exams in later phases). A
    SYSTEM admin may act on anything; a SCHOOL admin of the object's own
    school may act on it for oversight; otherwise only the object's owner
    (by default `created_by`) may act on it.

    has_permission() is left at BasePermission's default (True), so the
    real check runs at object-fetch time via has_object_permission — the
    normal DRF pattern for an "owner or admin" rule, since there's no
    object to check ownership against until then."""

    owner_field = 'created_by'

    def has_object_permission(self, request, view, obj):
        if not _authenticated(request):
            return False
        if is_system_admin(request.user):
            return True
        if request.user.role == ROLE_ADMIN and request.user.school_id == getattr(obj, 'school_id', None):
            return True
        return getattr(obj, f'{self.owner_field}_id', None) == request.user.id


class TenantScopedQuerysetMixin:
    """Mixin for viewsets whose queryset must be scoped to the requesting
    user's school. A SYSTEM admin sees every row; everyone else sees only
    rows belonging to their own school — derived solely from
    `request.user.school_id`, never from a client-supplied school_id."""

    tenant_field = 'school'

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if is_system_admin(user):
            return queryset
        return queryset.filter(**{self.tenant_field: getattr(user, 'school_id', None)})
