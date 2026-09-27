from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import ClassLevel
from apps.accounts.models import User
from apps.schools.models import School

from .models import SchoolClass, StudentClassMembership, TeacherClassMembership


def create_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    username = username or f'{role.lower()}-{school.code if school else "sys"}-{admin_scope or ""}'
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user


class ClassesTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name='School A', code='CLS-A')
        self.school_b = School.objects.create(name='School B', code='CLS-B')

        self.sys_admin = create_user(User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, username='cl_sysadmin')
        self.admin_a = create_user(
            User.Role.ADMIN, school=self.school_a, admin_scope=User.AdminScope.SCHOOL, username='cl_admin_a',
        )
        self.admin_b = create_user(
            User.Role.ADMIN, school=self.school_b, admin_scope=User.AdminScope.SCHOOL, username='cl_admin_b',
        )
        self.teacher_a = create_user(User.Role.TEACHER, school=self.school_a, username='cl_teacher_a')
        self.teacher_b = create_user(User.Role.TEACHER, school=self.school_b, username='cl_teacher_b')
        self.student_a = create_user(User.Role.STUDENT, school=self.school_a, username='cl_student_a')
        self.student_b = create_user(User.Role.STUDENT, school=self.school_b, username='cl_student_b')

        self.form_2 = ClassLevel.objects.get(code='F2')

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')


class SchoolClassCRUDTests(ClassesTestBase):
    def test_admin_can_create_class(self):
        self.auth('cl_admin_a')
        response = self.client.post('/api/classes/', {
            'name': 'Form II Blue', 'class_level': self.form_2.id, 'academic_year': 2026,
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['school'], self.school_a.id)

    def test_class_code_is_auto_generated_and_unique(self):
        self.auth('cl_admin_a')
        r1 = self.client.post('/api/classes/', {
            'name': 'Form II Blue', 'class_level': self.form_2.id, 'academic_year': 2026,
        })
        r2 = self.client.post('/api/classes/', {
            'name': 'Form II Green', 'class_level': self.form_2.id, 'academic_year': 2026,
        })
        code1 = r1.data['data']['class_code']
        code2 = r2.data['data']['class_code']
        self.assertTrue(code1)
        self.assertEqual(len(code1), 8)
        self.assertNotEqual(code1, code2)

    def test_class_code_is_not_client_settable(self):
        self.auth('cl_admin_a')
        response = self.client.post('/api/classes/', {
            'name': 'Form II Blue', 'class_level': self.form_2.id, 'academic_year': 2026,
            'class_code': 'HACKED01',
        })
        self.assertEqual(response.status_code, 201)
        self.assertNotEqual(response.data['data']['class_code'], 'HACKED01')

    def test_teacher_cannot_create_class(self):
        self.auth('cl_teacher_a')
        response = self.client.post('/api/classes/', {
            'name': 'Form II Blue', 'class_level': self.form_2.id, 'academic_year': 2026,
        })
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_create_class(self):
        self.auth('cl_student_a')
        response = self.client.post('/api/classes/', {
            'name': 'Form II Blue', 'class_level': self.form_2.id, 'academic_year': 2026,
        })
        self.assertEqual(response.status_code, 403)

    def test_duplicate_class_name_same_school_year_rejected(self):
        SchoolClass.objects.create(school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2026)
        self.auth('cl_admin_a')
        response = self.client.post('/api/classes/', {
            'name': 'Form II Blue', 'class_level': self.form_2.id, 'academic_year': 2026,
        })
        self.assertEqual(response.status_code, 400)

    def test_same_class_name_different_year_allowed(self):
        SchoolClass.objects.create(school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2025)
        self.auth('cl_admin_a')
        response = self.client.post('/api/classes/', {
            'name': 'Form II Blue', 'class_level': self.form_2.id, 'academic_year': 2026,
        })
        self.assertEqual(response.status_code, 201)

    def test_school_admin_injected_school_id_ignored(self):
        self.auth('cl_admin_a')
        response = self.client.post('/api/classes/', {
            'name': 'Form II Blue', 'class_level': self.form_2.id, 'academic_year': 2026,
            'school': self.school_b.id,
        })
        self.assertEqual(response.data['data']['school'], self.school_a.id)

    def test_teacher_cannot_retrieve_other_school_class(self):
        class_b = SchoolClass.objects.create(school=self.school_b, name='F2B', class_level=self.form_2, academic_year=2026)
        self.auth('cl_teacher_a')
        response = self.client.get(f'/api/classes/{class_b.id}/')
        self.assertEqual(response.status_code, 404)

    def test_school_admin_cannot_update_other_school_class(self):
        class_b = SchoolClass.objects.create(school=self.school_b, name='F2B', class_level=self.form_2, academic_year=2026)
        self.auth('cl_admin_a')
        response = self.client.patch(f'/api/classes/{class_b.id}/', {'name': 'Hacked'})
        self.assertEqual(response.status_code, 404)


class TeacherMembershipTests(ClassesTestBase):
    def setUp(self):
        super().setUp()
        self.class_a = SchoolClass.objects.create(
            school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2026,
        )

    def test_admin_can_assign_teacher_to_class(self):
        self.auth('cl_admin_a')
        response = self.client.post(f'/api/classes/{self.class_a.id}/teachers/', {'user_id': self.teacher_a.id})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            TeacherClassMembership.objects.filter(school_class=self.class_a, teacher=self.teacher_a).exists(),
        )

    def test_admin_cannot_assign_teacher_from_other_school(self):
        self.auth('cl_admin_a')
        response = self.client.post(f'/api/classes/{self.class_a.id}/teachers/', {'user_id': self.teacher_b.id})
        self.assertEqual(response.status_code, 400)

    def test_admin_cannot_assign_student_as_teacher(self):
        self.auth('cl_admin_a')
        response = self.client.post(f'/api/classes/{self.class_a.id}/teachers/', {'user_id': self.student_a.id})
        self.assertEqual(response.status_code, 400)

    def test_teacher_cannot_assign_teacher(self):
        self.auth('cl_teacher_a')
        response = self.client.post(f'/api/classes/{self.class_a.id}/teachers/', {'user_id': self.teacher_a.id})
        self.assertEqual(response.status_code, 403)

    def test_duplicate_teacher_assignment_rejected(self):
        TeacherClassMembership.objects.create(school_class=self.class_a, teacher=self.teacher_a)
        self.auth('cl_admin_a')
        response = self.client.post(f'/api/classes/{self.class_a.id}/teachers/', {'user_id': self.teacher_a.id})
        self.assertEqual(response.status_code, 400)

    def test_admin_can_unassign_teacher(self):
        TeacherClassMembership.objects.create(school_class=self.class_a, teacher=self.teacher_a)
        self.auth('cl_admin_a')
        response = self.client.delete(f'/api/classes/{self.class_a.id}/teachers/', {'user_id': self.teacher_a.id})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            TeacherClassMembership.objects.filter(school_class=self.class_a, teacher=self.teacher_a).exists(),
        )

    def test_list_teachers_open_to_any_school_member(self):
        TeacherClassMembership.objects.create(school_class=self.class_a, teacher=self.teacher_a)
        self.auth('cl_student_a')
        response = self.client.get(f'/api/classes/{self.class_a.id}/teachers/')
        self.assertEqual(response.status_code, 200)
        usernames = {t['username'] for t in response.data['data']}
        self.assertEqual(usernames, {'cl_teacher_a'})

    def test_admin_cannot_manage_membership_for_other_school_class(self):
        class_b = SchoolClass.objects.create(school=self.school_b, name='F2B', class_level=self.form_2, academic_year=2026)
        self.auth('cl_admin_a')
        response = self.client.post(f'/api/classes/{class_b.id}/teachers/', {'user_id': self.teacher_b.id})
        self.assertEqual(response.status_code, 404)


class StudentMembershipTests(ClassesTestBase):
    def setUp(self):
        super().setUp()
        self.class_a = SchoolClass.objects.create(
            school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2026,
        )

    def test_admin_can_assign_student_to_class(self):
        self.auth('cl_admin_a')
        response = self.client.post(f'/api/classes/{self.class_a.id}/students/', {'user_id': self.student_a.id})
        self.assertEqual(response.status_code, 201)

    def test_admin_cannot_assign_student_from_other_school(self):
        self.auth('cl_admin_a')
        response = self.client.post(f'/api/classes/{self.class_a.id}/students/', {'user_id': self.student_b.id})
        self.assertEqual(response.status_code, 400)

    def test_student_cannot_assign_student(self):
        self.auth('cl_student_a')
        response = self.client.post(f'/api/classes/{self.class_a.id}/students/', {'user_id': self.student_a.id})
        self.assertEqual(response.status_code, 403)

    def test_admin_can_unassign_student(self):
        StudentClassMembership.objects.create(school_class=self.class_a, student=self.student_a)
        self.auth('cl_admin_a')
        response = self.client.delete(f'/api/classes/{self.class_a.id}/students/', {'user_id': self.student_a.id})
        self.assertEqual(response.status_code, 200)


class ClassCodeJoinTests(ClassesTestBase):
    def setUp(self):
        super().setUp()
        self.class_a = SchoolClass.objects.create(
            school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2026,
        )
        self.inactive_class_a = SchoolClass.objects.create(
            school=self.school_a, name='Form II Green', class_level=self.form_2, academic_year=2026, is_active=False,
        )

    def test_student_can_join_class_with_valid_code(self):
        self.auth('cl_student_a')
        response = self.client.post('/api/classes/join/', {'class_code': self.class_a.class_code})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            StudentClassMembership.objects.filter(school_class=self.class_a, student=self.student_a).exists(),
        )

    def test_student_cannot_join_with_invalid_code(self):
        self.auth('cl_student_a')
        response = self.client.post('/api/classes/join/', {'class_code': 'NOSUCHCODE'})
        self.assertEqual(response.status_code, 400)

    def test_student_from_other_school_cannot_join_with_valid_code_from_different_school(self):
        """The class_code is unguessable, but even a leaked/shared valid
        code from a different school must never grant cross-tenant
        membership."""
        self.auth('cl_student_b')
        response = self.client.post('/api/classes/join/', {'class_code': self.class_a.class_code})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            StudentClassMembership.objects.filter(school_class=self.class_a, student=self.student_b).exists(),
        )

    def test_student_cannot_join_same_class_twice(self):
        self.auth('cl_student_a')
        self.client.post('/api/classes/join/', {'class_code': self.class_a.class_code})
        response = self.client.post('/api/classes/join/', {'class_code': self.class_a.class_code})
        self.assertEqual(response.status_code, 400)

    def test_teacher_cannot_use_join_endpoint(self):
        self.auth('cl_teacher_a')
        response = self.client.post('/api/classes/join/', {'class_code': self.class_a.class_code})
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_join_inactive_class(self):
        self.auth('cl_student_a')
        response = self.client.post('/api/classes/join/', {'class_code': self.inactive_class_a.class_code})
        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_cannot_join(self):
        response = self.client.post('/api/classes/join/', {'class_code': self.class_a.class_code})
        self.assertEqual(response.status_code, 401)
