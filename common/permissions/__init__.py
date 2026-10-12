from rest_framework.permissions import BasePermission

ROLE_ADMIN = 'ADMIN'
ROLE_TEACHER = 'TEACHER'
ROLE_STUDENT = 'STUDENT'
ROLE_EXAM_MARKING_MANAGER = 'EXAM_MARKING_MANAGER'
ROLE_CHIEF_EXAMINER = 'CHIEF_EXAMINER'
ROLE_EXAMINER = 'EXAMINER'
ROLE_CHECKER = 'CHECKER'
ROLE_DATA_ENTRY_CLERK = 'DATA_ENTRY_CLERK'
ROLE_IT_SUPPORT = 'IT_SUPPORT'

SCOPE_SYSTEM = 'SYSTEM'
SCOPE_SCHOOL = 'SCHOOL'

MARKING_OPERATIONAL_ROLES = frozenset({
    ROLE_EXAM_MARKING_MANAGER,
    ROLE_EXAMINER,
})

MARKING_READ_ROLES = frozenset({
    ROLE_EXAM_MARKING_MANAGER,
    ROLE_EXAMINER,
})

SCHOOL_SCOPED_ROLES = frozenset({
    ROLE_TEACHER, ROLE_STUDENT,
    ROLE_EXAM_MARKING_MANAGER, ROLE_CHIEF_EXAMINER, ROLE_EXAMINER,
    ROLE_CHECKER, ROLE_DATA_ENTRY_CLERK, ROLE_IT_SUPPORT,
})

def _authenticated(request):
    return bool(request.user and request.user.is_authenticated)

def is_system_admin(user):
    """True only for a SYSTEM-scoped admin, who spans every school.
    Must NOT be used to grant routine marking-domain permissions."""
    return bool(
        user and user.is_authenticated
        and user.role == ROLE_ADMIN and user.admin_scope == SCOPE_SYSTEM
    )

def is_marking_role(user):
    """True if the user holds any operational marking role."""
    return bool(
        user and user.is_authenticated
        and user.role in MARKING_OPERATIONAL_ROLES
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

class IsExamMarkingManager(BasePermission):
    def has_permission(self, request, view):
        return _authenticated(request) and request.user.role == ROLE_EXAM_MARKING_MANAGER

class IsChiefExaminer(BasePermission):
    def has_permission(self, request, view):
        return _authenticated(request) and request.user.role == ROLE_CHIEF_EXAMINER

class IsExaminer(BasePermission):
    def has_permission(self, request, view):
        return _authenticated(request) and request.user.role == ROLE_EXAMINER

class IsChecker(BasePermission):
    def has_permission(self, request, view):
        return _authenticated(request) and request.user.role == ROLE_CHECKER

class IsDataEntryClerk(BasePermission):
    def has_permission(self, request, view):
        return _authenticated(request) and request.user.role == ROLE_DATA_ENTRY_CLERK

class IsITSupport(BasePermission):
    def has_permission(self, request, view):
        return _authenticated(request) and request.user.role == ROLE_IT_SUPPORT

class IsMarkingRole(BasePermission):
    """Any user with an active MVP marking role (EXAM_MARKING_MANAGER or
    EXAMINER). Deliberately excludes SYSTEM_ADMIN."""
    def has_permission(self, request, view):
        return is_marking_role(request.user)

class IsOwnSchool(BasePermission):
    """Object-level permission for the School model itself."""
    def has_object_permission(self, request, view, obj):
        if not _authenticated(request):
            return False
        if is_system_admin(request.user):
            return True
        return obj.id == request.user.school_id

class IsTenantObjectOwner(BasePermission):
    """Object-level permission for models with a `school` FK."""
    def has_object_permission(self, request, view, obj):
        if not _authenticated(request):
            return False
        if is_system_admin(request.user):
            return True
        return getattr(obj, 'school_id', None) == request.user.school_id

class IsOwnerOrSchoolAdmin(BasePermission):
    """Object-level permission for content a specific user authors within
    their school. A SYSTEM admin may act on anything; a SCHOOL admin of
    the object's own school may act on it for oversight; otherwise only
    the object's `created_by` owner may act on it."""

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
    rows belonging to their own school."""

    tenant_field = 'school'

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if is_system_admin(user):
            return queryset
        return queryset.filter(**{self.tenant_field: getattr(user, 'school_id', None)})
