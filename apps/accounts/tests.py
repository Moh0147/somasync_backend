from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.schools.models import School
from common.permissions import (
    IsAdmin, IsSchoolAdmin, IsStudent, IsSystemAdmin, IsTeacher,
)
from common.throttling import LoginRateThrottle

from .models import User


def make_user(role, admin_scope=None, school=None, **extra):
    defaults = {'username': f'{role.lower()}-{admin_scope or "x"}', 'password': 'pass12345'}
    defaults.update(extra)
    user = User(
        role=role, admin_scope=admin_scope, school=school,
        **{k: v for k, v in defaults.items() if k != 'password'},
    )
    user.set_password(defaults['password'])
    user.save()
    return user


class UserRoleModelTests(TestCase):
    def test_exactly_three_roles_exist(self):
        self.assertEqual(set(User.Role.values), {'ADMIN', 'TEACHER', 'STUDENT'})

    def test_createsuperuser_produces_a_valid_system_admin(self):
        # Django's default create_superuser() has no idea role/admin_scope
        # exist -- it used to leave role='' and blow up the
        # school_required_unless_system_admin CHECK constraint on save.
        user = User.objects.create_superuser(username='rootuser', email='root@example.com', password='pass12345')
        self.assertEqual(user.role, User.Role.ADMIN)
        self.assertEqual(user.admin_scope, User.AdminScope.SYSTEM)
        self.assertIsNone(user.school)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)

    def test_admin_requires_admin_scope(self):
        user = User(username='badadmin', role=User.Role.ADMIN, admin_scope=None)
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_non_admin_cannot_have_admin_scope(self):
        user = User(username='badteacher', role=User.Role.TEACHER, admin_scope=User.AdminScope.SYSTEM)
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_system_admin_is_valid(self):
        user = User(username='sysadmin', role=User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM)
        user.set_password('pass12345')
        user.full_clean()  # should not raise

    def test_school_admin_is_valid(self):
        school = School.objects.create(name='Valid School', code='VS001')
        user = User(username='schooladmin', role=User.Role.ADMIN, admin_scope=User.AdminScope.SCHOOL, school=school)
        user.set_password('pass12345')
        user.full_clean()  # should not raise

    def test_teacher_without_school_is_invalid(self):
        user = User(username='homelessteacher', role=User.Role.TEACHER)
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_school_admin_without_school_is_invalid(self):
        user = User(username='homelessadmin', role=User.Role.ADMIN, admin_scope=User.AdminScope.SCHOOL)
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_system_admin_with_school_is_invalid(self):
        school = School.objects.create(name='Another School', code='AS001')
        user = User(username='wanderingsysadmin', role=User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, school=school)
        with self.assertRaises(ValidationError):
            user.full_clean()

    def test_db_check_constraint_blocks_invalid_combo(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                User.objects.create(username='sneaky', role=User.Role.TEACHER, admin_scope=User.AdminScope.SYSTEM)


class LoginEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school = School.objects.create(name='Login Test School', code='LTS001')
        self.user = make_user(User.Role.TEACHER, school=self.school, password='correct-pass', username='teacher1')

    def test_login_success_returns_tokens_and_user(self):
        response = self.client.post('/api/auth/login/', {'username': 'teacher1', 'password': 'correct-pass'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success'])
        data = response.data['data']
        self.assertIn('access', data)
        self.assertIn('refresh', data)
        self.assertEqual(data['user']['role'], 'TEACHER')

    def test_login_invalid_credentials_rejected(self):
        response = self.client.post('/api/auth/login/', {'username': 'teacher1', 'password': 'wrong-pass'})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.data['success'])

    def test_login_unknown_user_rejected(self):
        response = self.client.post('/api/auth/login/', {'username': 'ghost', 'password': 'whatever'})
        self.assertEqual(response.status_code, 401)


class RefreshEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school = School.objects.create(name='Refresh Test School', code='RTS001')
        self.user = make_user(User.Role.STUDENT, school=self.school, password='correct-pass', username='student1')
        login = self.client.post('/api/auth/login/', {'username': 'student1', 'password': 'correct-pass'})
        self.refresh_token = login.data['data']['refresh']

    def test_refresh_returns_new_access_token(self):
        response = self.client.post('/api/auth/refresh/', {'refresh': self.refresh_token})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success'])
        self.assertIn('access', response.data['data'])

    def test_refresh_with_invalid_token_returns_401(self):
        response = self.client.post('/api/auth/refresh/', {'refresh': 'not-a-real-token'})
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.data['success'])


class LogoutEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school = School.objects.create(name='Logout Test School', code='LOT001')
        self.user = make_user(User.Role.TEACHER, school=self.school, password='correct-pass', username='logout-teacher')
        login = self.client.post('/api/auth/login/', {'username': 'logout-teacher', 'password': 'correct-pass'})
        self.access = login.data['data']['access']
        self.refresh = login.data['data']['refresh']

    def test_logout_requires_authentication(self):
        response = APIClient().post('/api/auth/logout/', {'refresh': self.refresh})
        self.assertEqual(response.status_code, 401)

    def test_logout_blacklists_refresh_token(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access}')
        response = self.client.post('/api/auth/logout/', {'refresh': self.refresh})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['success'])

        # The now-blacklisted refresh token must be rejected, even though
        # it hasn't expired yet.
        refresh_attempt = self.client.post('/api/auth/refresh/', {'refresh': self.refresh})
        self.assertEqual(refresh_attempt.status_code, 401)

    def test_logout_with_garbage_token_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access}')
        response = self.client.post('/api/auth/logout/', {'refresh': 'not-a-real-token'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['success'])

    def test_logout_missing_refresh_returns_400(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access}')
        response = self.client.post('/api/auth/logout/', {})
        self.assertEqual(response.status_code, 400)


@override_settings(THROTTLING_ENABLED=True)
class LoginThrottleTests(TestCase):
    """THROTTLING_ENABLED is off in test mode (development.py) so the rest
    of the suite isn't polluted by it -- this class explicitly opts back in
    to verify the login throttle actually works.

    DRF's SimpleRateThrottle subclasses bind THROTTLE_RATES to
    api_settings.DEFAULT_THROTTLE_RATES once at import time, as a plain
    class attribute -- @override_settings on REST_FRAMEWORK does NOT
    re-read it. Mutating the dict in place (it's the same object) is what
    actually changes the effective rate for the duration of a test.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.client = APIClient()
        self.school = School.objects.create(name='Throttle Test School', code='TTS001')
        self.user = make_user(User.Role.TEACHER, school=self.school, password='correct-pass', username='throttled')

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()

    @patch.dict(LoginRateThrottle.THROTTLE_RATES, {'login': '3/min'})
    def test_login_throttled_after_limit_exceeded(self):
        for _ in range(3):
            response = self.client.post('/api/auth/login/', {'username': 'throttled', 'password': 'wrong'})
            self.assertEqual(response.status_code, 401)

        response = self.client.post('/api/auth/login/', {'username': 'throttled', 'password': 'wrong'})
        self.assertEqual(response.status_code, 429)


class MeEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school = School.objects.create(name='Me Test School', code='MTS001')
        self.user = make_user(
            User.Role.ADMIN, admin_scope=User.AdminScope.SCHOOL, school=self.school,
            username='admin1', password='correct-pass',
        )

    def test_me_requires_authentication(self):
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, 401)

    def test_me_returns_authenticated_profile(self):
        login = self.client.post('/api/auth/login/', {'username': 'admin1', 'password': 'correct-pass'})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')
        response = self.client.get('/api/auth/me/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['username'], 'admin1')
        self.assertEqual(response.data['data']['role'], 'ADMIN')
        self.assertEqual(response.data['data']['admin_scope'], 'SCHOOL')


class RolePermissionClassTests(TestCase):
    class _Request:
        def __init__(self, user):
            self.user = user

    def test_is_admin_accepts_any_admin_scope(self):
        perm = IsAdmin()
        sys_admin = User(role=User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, is_active=True)
        school_admin = User(role=User.Role.ADMIN, admin_scope=User.AdminScope.SCHOOL, is_active=True)
        self.assertTrue(perm.has_permission(self._Request(sys_admin), None))
        self.assertTrue(perm.has_permission(self._Request(school_admin), None))

    def test_system_and_school_admin_scopes_are_distinct(self):
        sys_admin = User(role=User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, is_active=True)
        school_admin = User(role=User.Role.ADMIN, admin_scope=User.AdminScope.SCHOOL, is_active=True)

        self.assertTrue(IsSystemAdmin().has_permission(self._Request(sys_admin), None))
        self.assertFalse(IsSystemAdmin().has_permission(self._Request(school_admin), None))
        self.assertTrue(IsSchoolAdmin().has_permission(self._Request(school_admin), None))
        self.assertFalse(IsSchoolAdmin().has_permission(self._Request(sys_admin), None))

    def test_teacher_and_student_permissions(self):
        teacher = User(role=User.Role.TEACHER, is_active=True)
        student = User(role=User.Role.STUDENT, is_active=True)

        self.assertTrue(IsTeacher().has_permission(self._Request(teacher), None))
        self.assertFalse(IsTeacher().has_permission(self._Request(student), None))
        self.assertTrue(IsStudent().has_permission(self._Request(student), None))
        self.assertFalse(IsStudent().has_permission(self._Request(teacher), None))

    def test_unauthenticated_user_denied(self):
        class Anon:
            is_authenticated = False

        self.assertFalse(IsAdmin().has_permission(self._Request(Anon()), None))
