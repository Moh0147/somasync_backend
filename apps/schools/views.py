from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated

from apps.accounts.serializers import UserSerializer
from common.mixins import EnvelopeMixin
from common.permissions import IsOwnSchool, IsSystemAdmin, is_system_admin
from common.utils import api_response

from .models import School
from .serializers import SchoolAdminCreateSerializer, SchoolSerializer

User = get_user_model()


class SchoolViewSet(EnvelopeMixin, viewsets.ModelViewSet):
    """/api/schools/

    SYSTEM admin: full access to every school (create, update, deactivate,
    manage school admins). Everyone else (school admin, teacher, student):
    read-only access to their own school, never another one.

    Tenant scoping is always derived from request.user — a school_id
    supplied by the client is never trusted for authorization.
    """

    serializer_class = SchoolSerializer
    permission_classes = [IsAuthenticated, IsOwnSchool]
    # No PUT (partial updates only) and no hard DELETE — schools are
    # deactivated via the explicit `deactivate` action instead.
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    success_messages = {'create': 'School created', 'update': 'School updated'}

    def get_queryset(self):
        user = self.request.user
        if is_system_admin(user):
            return School.objects.all()
        return School.objects.filter(id=getattr(user, 'school_id', None))

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update'):
            return [IsAuthenticated(), IsSystemAdmin()]
        return super().get_permissions()

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsSystemAdmin])
    def deactivate(self, request, pk=None):
        school = self.get_object()
        school.is_active = False
        school.save(update_fields=['is_active', 'updated_at'])
        return api_response(data=self.get_serializer(school).data, message='School deactivated')

    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated, IsSystemAdmin])
    def activate(self, request, pk=None):
        school = self.get_object()
        school.is_active = True
        school.save(update_fields=['is_active', 'updated_at'])
        return api_response(data=self.get_serializer(school).data, message='School activated')

    @action(detail=True, methods=['get', 'post'], url_path='admins', permission_classes=[IsAuthenticated])
    def admins(self, request, pk=None):
        # get_object() applies get_queryset()'s tenant scoping, so a user
        # from another school gets a 404 here, never a 403 that would
        # confirm the school exists.
        school = self.get_object()

        if request.method == 'POST':
            if not is_system_admin(request.user):
                raise PermissionDenied('Only a system admin can create school admins.')
            serializer = SchoolAdminCreateSerializer(data=request.data, context={'school': school})
            serializer.is_valid(raise_exception=True)
            user = serializer.save()
            return api_response(
                data=UserSerializer(user).data, message='School admin created',
                status=status.HTTP_201_CREATED,
            )

        is_own_school_admin = request.user.role == User.Role.ADMIN and request.user.school_id == school.id
        if not (is_system_admin(request.user) or is_own_school_admin):
            raise PermissionDenied("Only a system admin or that school's own admin may list its admins.")

        admins_qs = User.objects.filter(school=school, role=User.Role.ADMIN, admin_scope=User.AdminScope.SCHOOL)
        return api_response(data=UserSerializer(admins_qs, many=True).data)
