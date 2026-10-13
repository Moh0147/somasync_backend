from django.contrib.auth import get_user_model
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.accounts.serializers import UserSerializer
from common.mixins import EnvelopeMixin, TenantOwnedViewSetMixin
from common.permissions import IsStudent, is_system_admin
from common.utils import api_response

from .models import SchoolClass, StudentClassMembership, TeacherClassMembership
from .serializers import AssignMemberSerializer, JoinClassSerializer, SchoolClassSerializer

User = get_user_model()

class SchoolClassViewSet(EnvelopeMixin, TenantOwnedViewSetMixin, viewsets.ModelViewSet):

    queryset = SchoolClass.objects.select_related('school', 'class_level')
    serializer_class = SchoolClassSerializer
    success_messages = {'create': 'Class created', 'update': 'Class updated'}

    def _validate_unique_name(self, school, name, academic_year, exclude_pk=None):
        qs = SchoolClass.objects.filter(school=school, name=name, academic_year=academic_year)
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        if qs.exists():
            raise ValidationError({'name': 'A class with this name already exists for this school and year.'})

    def perform_create(self, serializer):
        school = self.resolve_create_school(serializer)
        self._validate_unique_name(
            school, serializer.validated_data.get('name'), serializer.validated_data.get('academic_year'),
        )
        serializer.save(school=school)

    def perform_update(self, serializer):
        school = serializer.instance.school
        name = serializer.validated_data.get('name', serializer.instance.name)
        academic_year = serializer.validated_data.get('academic_year', serializer.instance.academic_year)
        self._validate_unique_name(school, name, academic_year, exclude_pk=serializer.instance.pk)
        serializer.save(school=school)

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='teachers', permission_classes=[IsAuthenticated])
    def teachers(self, request, pk=None):
        school_class = self.get_object()

        if request.method == 'GET':
            memberships = TeacherClassMembership.objects.filter(school_class=school_class).select_related('teacher')
            return api_response(data=UserSerializer([m.teacher for m in memberships], many=True).data)

        if not self._is_school_or_system_admin(request.user, school_class):
            raise PermissionDenied('Only an admin of this school may manage teacher assignments.')

        serializer = AssignMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        teacher = self._get_school_user(school_class, serializer.validated_data['user_id'], User.Role.TEACHER)

        if request.method == 'POST':
            _, created = TeacherClassMembership.objects.get_or_create(school_class=school_class, teacher=teacher)
            if not created:
                raise ValidationError('This teacher is already assigned to this class.')
            return api_response(
                data=UserSerializer(teacher).data, message='Teacher assigned', status=status.HTTP_201_CREATED,
            )

        deleted, _ = TeacherClassMembership.objects.filter(school_class=school_class, teacher=teacher).delete()
        if not deleted:
            raise ValidationError('This teacher is not assigned to this class.')
        return api_response(message='Teacher unassigned')

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='students', permission_classes=[IsAuthenticated])
    def students(self, request, pk=None):
        school_class = self.get_object()

        if request.method == 'GET':
            memberships = StudentClassMembership.objects.filter(school_class=school_class).select_related('student')
            return api_response(data=UserSerializer([m.student for m in memberships], many=True).data)

        if not self._is_school_or_system_admin(request.user, school_class):
            raise PermissionDenied('Only an admin of this school may manage student enrollment.')

        serializer = AssignMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = self._get_school_user(school_class, serializer.validated_data['user_id'], User.Role.STUDENT)

        if request.method == 'POST':
            _, created = StudentClassMembership.objects.get_or_create(school_class=school_class, student=student)
            if not created:
                raise ValidationError('This student is already enrolled in this class.')
            return api_response(
                data=UserSerializer(student).data, message='Student enrolled', status=status.HTTP_201_CREATED,
            )

        deleted, _ = StudentClassMembership.objects.filter(school_class=school_class, student=student).delete()
        if not deleted:
            raise ValidationError('This student is not enrolled in this class.')
        return api_response(message='Student unenrolled')

    @action(detail=False, methods=['post'], url_path='join', permission_classes=[IsAuthenticated, IsStudent])
    def join(self, request):
        serializer = JoinClassSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data['class_code']

        school_class = SchoolClass.objects.filter(class_code=code).first()
        if school_class is None or school_class.school_id != request.user.school_id:
            raise ValidationError({'class_code': 'Invalid class code.'})

        if not school_class.is_active:
            raise ValidationError({'class_code': 'This class is no longer active.'})

        _, created = StudentClassMembership.objects.get_or_create(school_class=school_class, student=request.user)
        if not created:
            raise ValidationError('You are already enrolled in this class.')

        return api_response(
            data=SchoolClassSerializer(school_class).data, message='Joined class', status=status.HTTP_201_CREATED,
        )

    def _is_school_or_system_admin(self, user, school_class):
        return is_system_admin(user) or (user.role == User.Role.ADMIN and user.school_id == school_class.school_id)

    def _get_school_user(self, school_class, user_id, required_role):
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise ValidationError({'user_id': 'No such user.'})
        if user.role != required_role:
            raise ValidationError({'user_id': f'User must have role={required_role}.'})
        if user.school_id != school_class.school_id:
            raise ValidationError({'user_id': "User does not belong to this class's school."})
        return user
