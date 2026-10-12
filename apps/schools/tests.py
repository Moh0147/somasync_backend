from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User

from .models import School

def create_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    username = username or f'{role.lower()}-{school.code if school else "sys"}-{admin_scope or ""}'
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user

class SchoolTenantIsolationTests(TestCase):
    """Phase 3's core requirement: School A must never be able to see,
    modify, or otherwise reach School B's data — and school_id supplied by
    the client must never be trusted for authorization or ownership."""

    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name='School A', code='SCA001')
        self.school_b = School.objects.create(name='School B', code='SCB001')

        self.sys_admin = create_user(User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, username='sysadmin')
        self.admin_a = create_user(
            User.Role.ADMIN, school=self.school_a, admin_scope=User.AdminScope.SCHOOL, username='admin_a',
        )
        self.admin_b = create_user(
            User.Role.ADMIN, school=self.school_b, admin_scope=User.AdminScope.SCHOOL, username='admin_b',
        )
        self.teacher_a = create_user(User.Role.TEACHER, school=self.school_a, username='teacher_a')
        self.student_a = create_user(User.Role.STUDENT, school=self.school_a, username='student_a')

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

    def test_system_admin_can_create_school(self):
        self.auth('sysadmin')
        response = self.client.post(
            '/api/schools/',
            {'name': 'New School', 'code': 'NEW001'},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['code'], 'NEW001')

    def test_school_admin_cannot_create_school(self):
        self.auth('admin_a')
        response = self.client.post('/api/schools/', {'name': 'Sneaky School', 'code': 'SNK001'})
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_create_school(self):
        self.auth('teacher_a')
        response = self.client.post('/api/schools/', {'name': 'Sneaky School 2', 'code': 'SNK002'})
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_request_rejected(self):
        response = self.client.get('/api/schools/')
        self.assertEqual(response.status_code, 401)

    def test_system_admin_sees_all_schools(self):
        self.auth('sysadmin')
        response = self.client.get('/api/schools/')
        codes = {s['code'] for s in response.data['data']['results']}
        self.assertEqual(codes, {'SCA001', 'SCB001'})

    def test_school_admin_sees_only_own_school_in_list(self):
        self.auth('admin_a')
        response = self.client.get('/api/schools/')
        codes = {s['code'] for s in response.data['data']['results']}
        self.assertEqual(codes, {'SCA001'})

    def test_teacher_sees_only_own_school_in_list(self):
        self.auth('teacher_a')
        response = self.client.get('/api/schools/')
        codes = {s['code'] for s in response.data['data']['results']}
        self.assertEqual(codes, {'SCA001'})

    def test_school_admin_can_retrieve_own_school(self):
        self.auth('admin_a')
        response = self.client.get(f'/api/schools/{self.school_a.id}/')
        self.assertEqual(response.status_code, 200)

    def test_school_admin_cannot_retrieve_other_school(self):
        self.auth('admin_a')
        response = self.client.get(f'/api/schools/{self.school_b.id}/')
        self.assertEqual(response.status_code, 404)

    def test_teacher_cannot_retrieve_other_school(self):
        self.auth('teacher_a')
        response = self.client.get(f'/api/schools/{self.school_b.id}/')
        self.assertEqual(response.status_code, 404)

    def test_student_cannot_retrieve_other_school(self):
        self.auth('student_a')
        response = self.client.get(f'/api/schools/{self.school_b.id}/')
        self.assertEqual(response.status_code, 404)

    def test_system_admin_can_retrieve_any_school(self):
        self.auth('sysadmin')
        response = self.client.get(f'/api/schools/{self.school_b.id}/')
        self.assertEqual(response.status_code, 200)

    def test_system_admin_can_update_any_school(self):
        self.auth('sysadmin')
        response = self.client.patch(f'/api/schools/{self.school_b.id}/', {'name': 'Renamed B'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['name'], 'Renamed B')

    def test_school_admin_cannot_update_own_school(self):
        self.auth('admin_a')
        response = self.client.patch(f'/api/schools/{self.school_a.id}/', {'name': 'Hacked A'})
        self.assertEqual(response.status_code, 403)

    def test_school_admin_cannot_update_other_school(self):
        self.auth('admin_a')
        response = self.client.patch(f'/api/schools/{self.school_b.id}/', {'name': 'Hacked B'})
        self.assertIn(response.status_code, (403, 404))

    def test_system_admin_can_deactivate_school(self):
        self.auth('sysadmin')
        response = self.client.post(f'/api/schools/{self.school_b.id}/deactivate/')
        self.assertEqual(response.status_code, 200)
        self.school_b.refresh_from_db()
        self.assertFalse(self.school_b.is_active)

    def test_school_admin_cannot_deactivate_school(self):
        self.auth('admin_a')
        response = self.client.post(f'/api/schools/{self.school_a.id}/deactivate/')
        self.assertEqual(response.status_code, 403)

    def test_system_admin_can_create_school_admin_for_any_school(self):
        self.auth('sysadmin')
        response = self.client.post(
            f'/api/schools/{self.school_b.id}/admins/',
            {'username': 'newadmin_b', 'password': 'strongpass123'},
        )
        self.assertEqual(response.status_code, 201)
        created = User.objects.get(username='newadmin_b')
        self.assertEqual(created.school_id, self.school_b.id)
        self.assertEqual(created.admin_scope, User.AdminScope.SCHOOL)

    def test_school_admin_cannot_create_school_admin(self):
        self.auth('admin_a')
        response = self.client.post(
            f'/api/schools/{self.school_a.id}/admins/',
            {'username': 'sneaky_admin', 'password': 'strongpass123'},
        )
        self.assertEqual(response.status_code, 403)

    def test_school_admin_cannot_create_admin_for_other_school(self):
        self.auth('admin_a')
        response = self.client.post(
            f'/api/schools/{self.school_b.id}/admins/',
            {'username': 'sneaky_admin_b', 'password': 'strongpass123'},
        )
        self.assertEqual(response.status_code, 404)

    def test_injected_school_id_in_body_is_ignored(self):
        """The URL's school (permission-checked via get_object) governs
        ownership, not a school_id/school the client sneaks into the body."""
        self.auth('sysadmin')
        response = self.client.post(
            f'/api/schools/{self.school_a.id}/admins/',
            {'username': 'triesescalation', 'password': 'strongpass123', 'school': self.school_b.id},
        )
        self.assertEqual(response.status_code, 201)
        created = User.objects.get(username='triesescalation')
        self.assertEqual(created.school_id, self.school_a.id)

    def test_own_school_admin_can_list_own_school_admins(self):
        self.auth('admin_a')
        response = self.client.get(f'/api/schools/{self.school_a.id}/admins/')
        self.assertEqual(response.status_code, 200)
        usernames = {a['username'] for a in response.data['data']}
        self.assertIn('admin_a', usernames)

    def test_teacher_cannot_list_school_admins(self):
        self.auth('teacher_a')
        response = self.client.get(f'/api/schools/{self.school_a.id}/admins/')
        self.assertEqual(response.status_code, 403)

    def test_school_admin_cannot_list_other_school_admins(self):
        self.auth('admin_a')
        response = self.client.get(f'/api/schools/{self.school_b.id}/admins/')
        self.assertEqual(response.status_code, 404)
