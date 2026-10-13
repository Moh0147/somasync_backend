from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView as BaseTokenRefreshView

from common.permissions import IsSystemAdmin, is_system_admin
from common.throttling import LoginRateThrottle
from common.utils import api_response

from .serializers import (
    CreateUserSerializer, CustomTokenObtainPairSerializer,
    LogoutSerializer, UpdateUserSerializer, UserSerializer,
)

User = get_user_model()

class LoginView(TokenObtainPairView):

    serializer_class = CustomTokenObtainPairSerializer
    throttle_scope = 'login'

    def get_throttles(self):
        if not settings.THROTTLING_ENABLED:
            return []
        return [LoginRateThrottle()]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])
        return api_response(data=serializer.validated_data, message='Login successful')

class TokenRefreshView(BaseTokenRefreshView):

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])
        return api_response(data=serializer.validated_data, message='Token refreshed')

class MeView(APIView):

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return api_response(data=UserSerializer(request.user).data)

class LogoutView(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data['refresh']).blacklist()
        except TokenError as e:
            raise ValidationError({'refresh': str(e)})
        return api_response(message='Logged out')

_SYSTEM_ADMIN_FILTER = {'role': User.Role.ADMIN, 'admin_scope': User.AdminScope.SYSTEM}

def _user_queryset():
    return (
        User.objects
        .select_related('school')
        .exclude(**_SYSTEM_ADMIN_FILTER)
        .order_by('school__name', 'role', 'last_name', 'first_name', 'username')
    )

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated, IsSystemAdmin])
def user_list_create(request):

    if request.method == 'GET':
        users = _user_queryset()
        return api_response(data=UserSerializer(users, many=True).data)

    serializer = CreateUserSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    d = serializer.validated_data

    if User.objects.filter(username=d['username']).exists():
        raise ValidationError({'username': 'A user with that username already exists.'})

    user = User(
        username=d['username'],
        email=d.get('email', ''),
        first_name=d.get('first_name', ''),
        last_name=d.get('last_name', ''),
        phone_number=d.get('phone_number', ''),
        role=d['role'],
        school=d.get('school'),
    )
    user.set_password(d['password'])
    user.full_clean()
    user.save()
    return api_response(data=UserSerializer(user).data, status=201, message='User created')

@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated, IsSystemAdmin])
def user_detail(request, pk):
 
    try:
        user = _user_queryset().get(pk=pk)
    except User.DoesNotExist:
        return api_response(status=404, message='User not found')

    if request.method == 'GET':
        return api_response(data=UserSerializer(user).data)

    serializer = UpdateUserSerializer(data=request.data, context={'instance': user})
    serializer.is_valid(raise_exception=True)
    d = serializer.validated_data

    for field in ('first_name', 'last_name', 'email', 'phone_number', 'is_active'):
        if field in d:
            setattr(user, field, d[field])

    if 'role' in d:
        user.role = d['role']
    if 'secondary_role' in d:
        user.secondary_role = d['secondary_role'] or None
    if 'school' in d:
        user.school = d['school']

    user.full_clean()
    user.save()
    return api_response(data=UserSerializer(user).data, message='User updated')

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def school_teachers(request):

    user = request.user
    if user.role != 'EXAM_MARKING_MANAGER' and not is_system_admin(user):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('Only an Exam Marking Manager may access this endpoint.')
    if not user.school_id and not is_system_admin(user):
        return api_response(data=[])
    qs = User.objects.filter(school_id=user.school_id, role='TEACHER').order_by(
        'last_name', 'first_name', 'username',
    )
    data = [
        {
            'id': u.id,
            'username': u.username,
            'full_name': f'{u.first_name} {u.last_name}'.strip() or u.username,
            'secondary_role': u.secondary_role,
            'is_examiner': u.secondary_role == 'EXAMINER',
        }
        for u in qs
    ]
    return api_response(data=data)

@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
def assign_examiner_role(request, pk):

    caller = request.user
    if caller.role != 'EXAM_MARKING_MANAGER' and not is_system_admin(caller):
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('Only an Exam Marking Manager may assign examiner roles.')

    try:
        teacher = User.objects.get(pk=pk, role='TEACHER')
    except User.DoesNotExist:
        return api_response(status=404, message='Teacher not found.')

    if not is_system_admin(caller) and teacher.school_id != caller.school_id:
        from rest_framework.exceptions import PermissionDenied
        raise PermissionDenied('Teacher must be at the same school.')

    if request.method == 'POST':
        teacher.secondary_role = 'EXAMINER'
        teacher.save(update_fields=['secondary_role'])
        return api_response(
            data={'id': teacher.id, 'full_name': f'{teacher.first_name} {teacher.last_name}'.strip() or teacher.username, 'secondary_role': 'EXAMINER'},
            message='Examiner role assigned.',
        )

    teacher.secondary_role = None
    teacher.save(update_fields=['secondary_role'])
    return api_response(
        data={'id': teacher.id, 'full_name': f'{teacher.first_name} {teacher.last_name}'.strip() or teacher.username, 'secondary_role': None},
        message='Examiner role removed.',
    )
