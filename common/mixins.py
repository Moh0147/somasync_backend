from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from common.permissions import IsAdmin, TenantScopedQuerysetMixin, is_system_admin
from common.utils import api_response

class EnvelopeMixin:
    """Wraps a ModelViewSet's default list/retrieve/create/update output in
    the project's standard {success, data} response envelope, so DRF's
    generic behavior doesn't have to be reimplemented by hand in every
    viewset just to match Phase 1's response conventions."""

    success_messages = {}

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return api_response(data=response.data, status=response.status_code)

    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        return api_response(data=response.data, status=response.status_code)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        return api_response(
            data=response.data, status=response.status_code,
            message=self.success_messages.get('create'),
        )

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        return api_response(
            data=response.data, status=response.status_code,
            message=self.success_messages.get('update'),
        )

class TenantOwnedViewSetMixin(TenantScopedQuerysetMixin):
    """For ViewSets over models with a `school` FK, owned by a specific
    tenant (subjects, topics, classes, lessons, quizzes, ...). Combines
    queryset scoping with:
      - write actions (create/update/partial_update/destroy) require an
        ADMIN, system or school scoped;
      - on create, a school-scoped admin's `school` is always forced to
        their own school — a client-supplied school is never trusted; a
        system admin must supply one explicitly, since they span all
        schools;
      - on update, `school` can never change — the tenant boundary is
        immutable once a row exists.

    Cross-tenant FK references (e.g. a Topic's `subject` belonging to a
    different school than the Topic itself) are NOT caught here — that
    depends on the specific model's relationships, so subclasses validate
    it in their own perform_create/perform_update.
    """

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy'):
            return [IsAuthenticated(), IsAdmin()]
        return super().get_permissions()

    def resolve_create_school(self, serializer):
        user = self.request.user
        if is_system_admin(user):
            school = serializer.validated_data.get('school')
            if not school:
                raise ValidationError({'school': 'This field is required for a system admin.'})
            return school
        return user.school

    def perform_create(self, serializer):
        serializer.save(school=self.resolve_create_school(serializer))

    def perform_update(self, serializer):
        serializer.save(school=serializer.instance.school)
