from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import ClassLevel, Subject
from apps.accounts.models import User
from apps.classes.models import SchoolClass, StudentClassMembership
from apps.lessons.models import Lesson, LessonAssignment
from apps.schools.models import School

from .models import LessonProgress


def create_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    username = username or f'{role.lower()}-{school.code if school else "sys"}-{admin_scope or ""}'
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user


class ProgressTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name='School A', code='PR-A')
        self.school_b = School.objects.create(name='School B', code='PR-B')

        self.sys_admin = create_user(User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, username='pr_sysadmin')
        self.admin_a = create_user(
            User.Role.ADMIN, school=self.school_a, admin_scope=User.AdminScope.SCHOOL, username='pr_admin_a',
        )
        self.teacher_a = create_user(User.Role.TEACHER, school=self.school_a, username='pr_teacher_a')
        self.student_a = create_user(User.Role.STUDENT, school=self.school_a, username='pr_student_a')
        self.student_a2 = create_user(User.Role.STUDENT, school=self.school_a, username='pr_student_a2')
        self.student_b = create_user(User.Role.STUDENT, school=self.school_b, username='pr_student_b')

        self.form_2 = ClassLevel.objects.get(code='F2')
        self.subject_a = Subject.objects.create(school=self.school_a, name='Mathematics')
        self.class_a = SchoolClass.objects.create(
            school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2026,
        )
        StudentClassMembership.objects.create(school_class=self.class_a, student=self.student_a)
        # student_a2 deliberately NOT enrolled in any class.

        self.lesson = Lesson.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2,
            title='Intro to Algebra', content='x + 2 = 5', created_by=self.teacher_a,
            status=Lesson.Status.PUBLISHED,
        )
        LessonAssignment.objects.create(lesson=self.lesson, school_class=self.class_a, assigned_by=self.teacher_a)

        self.draft_lesson = Lesson.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2,
            title='Unpublished Lesson', created_by=self.teacher_a,
        )

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')


class TrackProgressTests(ProgressTestBase):
    def test_student_can_create_progress(self):
        self.auth('pr_student_a')
        response = self.client.post('/api/progress/track/', {
            'lesson': self.lesson.id, 'progress_percentage': 40, 'last_position': 'para-3',
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['progress_percentage'], 40)
        self.assertEqual(response.data['data']['student'], self.student_a.id)
        self.assertFalse(response.data['data']['completed'])

    def test_tracking_again_updates_not_duplicates(self):
        self.auth('pr_student_a')
        self.client.post('/api/progress/track/', {'lesson': self.lesson.id, 'progress_percentage': 40})
        response = self.client.post('/api/progress/track/', {
            'lesson': self.lesson.id, 'progress_percentage': 90, 'completed': True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['progress_percentage'], 90)
        self.assertTrue(response.data['data']['completed'])
        self.assertEqual(LessonProgress.objects.filter(student=self.student_a, lesson=self.lesson).count(), 1)

    def test_progress_percentage_over_100_rejected(self):
        self.auth('pr_student_a')
        response = self.client.post('/api/progress/track/', {'lesson': self.lesson.id, 'progress_percentage': 150})
        self.assertEqual(response.status_code, 400)

    def test_progress_percentage_negative_rejected(self):
        self.auth('pr_student_a')
        response = self.client.post('/api/progress/track/', {'lesson': self.lesson.id, 'progress_percentage': -5})
        self.assertEqual(response.status_code, 400)

    def test_teacher_cannot_track_progress(self):
        self.auth('pr_teacher_a')
        response = self.client.post('/api/progress/track/', {'lesson': self.lesson.id, 'progress_percentage': 10})
        self.assertEqual(response.status_code, 403)

    def test_cannot_track_progress_for_draft_lesson(self):
        self.auth('pr_student_a')
        response = self.client.post('/api/progress/track/', {'lesson': self.draft_lesson.id, 'progress_percentage': 10})
        self.assertEqual(response.status_code, 400)

    def test_cannot_track_progress_for_unassigned_lesson(self):
        self.auth('pr_student_a2')  # not enrolled in any class
        response = self.client.post('/api/progress/track/', {'lesson': self.lesson.id, 'progress_percentage': 10})
        self.assertEqual(response.status_code, 400)

    def test_student_from_other_school_cannot_track_progress(self):
        self.auth('pr_student_b')
        response = self.client.post('/api/progress/track/', {'lesson': self.lesson.id, 'progress_percentage': 10})
        self.assertEqual(response.status_code, 400)

    def test_client_supplied_last_accessed_at_is_respected(self):
        self.auth('pr_student_a')
        response = self.client.post('/api/progress/track/', {
            'lesson': self.lesson.id, 'progress_percentage': 20,
            'last_accessed_at': '2026-01-01T08:00:00Z',
        })
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.data['data']['last_accessed_at'].startswith('2026-01-01T08:00:00'))

    def test_last_accessed_at_defaults_to_now_when_omitted(self):
        self.auth('pr_student_a')
        response = self.client.post('/api/progress/track/', {'lesson': self.lesson.id, 'progress_percentage': 20})
        self.assertIsNotNone(response.data['data']['last_accessed_at'])

    def test_student_field_is_never_client_settable(self):
        self.auth('pr_student_a')
        response = self.client.post('/api/progress/track/', {
            'lesson': self.lesson.id, 'progress_percentage': 20, 'student': self.student_a2.id,
        })
        self.assertEqual(response.data['data']['student'], self.student_a.id)


class ProgressOwnershipAndVisibilityTests(ProgressTestBase):
    def setUp(self):
        super().setUp()
        self.auth('pr_student_a')
        self.client.post('/api/progress/track/', {'lesson': self.lesson.id, 'progress_percentage': 50})

    def test_student_sees_own_progress(self):
        response = self.client.get('/api/progress/')
        self.assertEqual(response.data['data']['count'], 1)

    def test_student_does_not_see_another_students_progress(self):
        self.auth('pr_student_a2')
        response = self.client.get('/api/progress/')
        self.assertEqual(response.data['data']['count'], 0)

    def test_student_cannot_retrieve_another_students_progress_by_id(self):
        progress_id = LessonProgress.objects.get(student=self.student_a, lesson=self.lesson).id
        self.auth('pr_student_a2')
        response = self.client.get(f'/api/progress/{progress_id}/')
        self.assertEqual(response.status_code, 404)

    def test_teacher_sees_progress_for_their_own_lesson(self):
        self.auth('pr_teacher_a')
        response = self.client.get('/api/progress/')
        self.assertEqual(response.data['data']['count'], 1)

    def test_admin_sees_progress_in_their_school(self):
        self.auth('pr_admin_a')
        response = self.client.get('/api/progress/')
        self.assertEqual(response.data['data']['count'], 1)

    def test_system_admin_sees_all_progress(self):
        self.auth('pr_sysadmin')
        response = self.client.get('/api/progress/')
        self.assertEqual(response.data['data']['count'], 1)

    def test_unauthenticated_cannot_access_progress(self):
        self.client.credentials()
        response = self.client.get('/api/progress/')
        self.assertEqual(response.status_code, 401)


class ProgressSchoolIsolationTests(ProgressTestBase):
    def setUp(self):
        super().setUp()
        self.auth('pr_student_a')
        self.client.post('/api/progress/track/', {'lesson': self.lesson.id, 'progress_percentage': 50})

        # School B's own lesson + progress, for isolation checks.
        self.subject_b = Subject.objects.create(school=self.school_b, name='Mathematics')
        self.class_b = SchoolClass.objects.create(
            school=self.school_b, name='F2B', class_level=self.form_2, academic_year=2026,
        )
        StudentClassMembership.objects.create(school_class=self.class_b, student=self.student_b)
        self.teacher_b = create_user(User.Role.TEACHER, school=self.school_b, username='pr_teacher_b')
        self.lesson_b = Lesson.objects.create(
            school=self.school_b, subject=self.subject_b, class_level=self.form_2,
            title='School B Lesson', created_by=self.teacher_b, status=Lesson.Status.PUBLISHED,
        )
        LessonAssignment.objects.create(lesson=self.lesson_b, school_class=self.class_b, assigned_by=self.teacher_b)
        self.auth('pr_student_b')
        self.client.post('/api/progress/track/', {'lesson': self.lesson_b.id, 'progress_percentage': 30})

    def test_admin_a_does_not_see_school_b_progress(self):
        self.auth('pr_admin_a')
        response = self.client.get('/api/progress/')
        lesson_ids = {p['lesson'] for p in response.data['data']['results']}
        self.assertEqual(lesson_ids, {self.lesson.id})

    def test_system_admin_sees_progress_from_both_schools(self):
        self.auth('pr_sysadmin')
        response = self.client.get('/api/progress/')
        self.assertEqual(response.data['data']['count'], 2)

    def test_teacher_a_does_not_see_teacher_bs_lesson_progress(self):
        self.auth('pr_teacher_a')
        response = self.client.get('/api/progress/')
        lesson_ids = {p['lesson'] for p in response.data['data']['results']}
        self.assertEqual(lesson_ids, {self.lesson.id})
