from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import User
from apps.schools.models import School

from .models import ClassLevel, LearningObjective, Subject, Topic

def create_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    username = username or f'{role.lower()}-{school.code if school else "sys"}-{admin_scope or ""}'
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user

class AcademicsTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name='School A', code='ACA-A')
        self.school_b = School.objects.create(name='School B', code='ACA-B')

        self.sys_admin = create_user(User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, username='ac_sysadmin')
        self.admin_a = create_user(
            User.Role.ADMIN, school=self.school_a, admin_scope=User.AdminScope.SCHOOL, username='ac_admin_a',
        )
        self.admin_b = create_user(
            User.Role.ADMIN, school=self.school_b, admin_scope=User.AdminScope.SCHOOL, username='ac_admin_b',
        )
        self.teacher_a = create_user(User.Role.TEACHER, school=self.school_a, username='ac_teacher_a')
        self.student_a = create_user(User.Role.STUDENT, school=self.school_a, username='ac_student_a')

        self.form_1 = ClassLevel.objects.get(code='F1')
        self.form_2 = ClassLevel.objects.get(code='F2')

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

class ClassLevelTests(AcademicsTestBase):
    def test_seed_data_has_four_forms(self):
        self.assertEqual(
            set(ClassLevel.objects.values_list('code', flat=True)),
            {'F1', 'F2', 'F3', 'F4'},
        )

    def test_unauthenticated_cannot_list_class_levels(self):
        response = self.client.get('/api/academics/class-levels/')
        self.assertEqual(response.status_code, 401)

    def test_any_authenticated_role_can_list_class_levels(self):
        self.auth('ac_teacher_a')
        response = self.client.get('/api/academics/class-levels/')
        self.assertEqual(response.status_code, 200)
        codes = {c['code'] for c in response.data['data']['results']}
        self.assertEqual(codes, {'F1', 'F2', 'F3', 'F4'})

    def test_system_admin_can_create_class_level(self):
        self.auth('ac_sysadmin')
        response = self.client.post('/api/academics/class-levels/', {'name': 'Form V', 'code': 'F5', 'order': 5})
        self.assertEqual(response.status_code, 201)

    def test_school_admin_cannot_create_class_level(self):
        self.auth('ac_admin_a')
        response = self.client.post('/api/academics/class-levels/', {'name': 'Form V', 'code': 'F5', 'order': 5})
        self.assertEqual(response.status_code, 403)

class SubjectTests(AcademicsTestBase):
    def test_system_admin_can_create_subject_for_any_school(self):
        self.auth('ac_sysadmin')
        response = self.client.post(
            '/api/academics/subjects/', {'name': 'Mathematics', 'school': self.school_b.id},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['school'], self.school_b.id)

    def test_system_admin_create_without_school_fails(self):
        self.auth('ac_sysadmin')
        response = self.client.post('/api/academics/subjects/', {'name': 'Mathematics'})
        self.assertEqual(response.status_code, 400)

    def test_school_admin_can_create_subject_for_own_school(self):
        self.auth('ac_admin_a')
        response = self.client.post('/api/academics/subjects/', {'name': 'Biology'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['school'], self.school_a.id)

    def test_school_admin_injected_school_id_is_ignored(self):
        self.auth('ac_admin_a')
        response = self.client.post(
            '/api/academics/subjects/', {'name': 'Chemistry', 'school': self.school_b.id},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['school'], self.school_a.id)

    def test_teacher_cannot_create_subject(self):
        self.auth('ac_teacher_a')
        response = self.client.post('/api/academics/subjects/', {'name': 'Physics'})
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_create_subject(self):
        self.auth('ac_student_a')
        response = self.client.post('/api/academics/subjects/', {'name': 'Physics'})
        self.assertEqual(response.status_code, 403)

    def test_duplicate_subject_name_in_same_school_rejected(self):
        Subject.objects.create(school=self.school_a, name='Geography')
        self.auth('ac_admin_a')
        response = self.client.post('/api/academics/subjects/', {'name': 'Geography'})
        self.assertEqual(response.status_code, 400)

    def test_same_subject_name_allowed_in_different_schools(self):
        Subject.objects.create(school=self.school_a, name='History')
        self.auth('ac_admin_b')
        response = self.client.post('/api/academics/subjects/', {'name': 'History'})
        self.assertEqual(response.status_code, 201)

    def test_teacher_can_list_own_school_subjects(self):
        Subject.objects.create(school=self.school_a, name='Civics')
        Subject.objects.create(school=self.school_b, name='Commerce')
        self.auth('ac_teacher_a')
        response = self.client.get('/api/academics/subjects/')
        names = {s['name'] for s in response.data['data']['results']}
        self.assertEqual(names, {'Civics'})

    def test_teacher_cannot_retrieve_other_school_subject(self):
        subject_b = Subject.objects.create(school=self.school_b, name='Commerce')
        self.auth('ac_teacher_a')
        response = self.client.get(f'/api/academics/subjects/{subject_b.id}/')
        self.assertEqual(response.status_code, 404)

    def test_school_admin_cannot_update_other_school_subject(self):
        subject_b = Subject.objects.create(school=self.school_b, name='Commerce')
        self.auth('ac_admin_a')
        response = self.client.patch(f'/api/academics/subjects/{subject_b.id}/', {'name': 'Hacked'})
        self.assertEqual(response.status_code, 404)

    def test_school_admin_can_update_own_subject(self):
        subject_a = Subject.objects.create(school=self.school_a, name='Kiswahili')
        self.auth('ac_admin_a')
        response = self.client.patch(f'/api/academics/subjects/{subject_a.id}/', {'name': 'Kiswahili I'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['name'], 'Kiswahili I')

    def test_school_admin_cannot_reassign_subject_to_other_school(self):
        subject_a = Subject.objects.create(school=self.school_a, name='English')
        self.auth('ac_admin_a')
        response = self.client.patch(
            f'/api/academics/subjects/{subject_a.id}/', {'school': self.school_b.id},
        )
        self.assertEqual(response.status_code, 200)
        subject_a.refresh_from_db()
        self.assertEqual(subject_a.school_id, self.school_a.id)

    def test_school_admin_can_delete_own_subject(self):
        subject_a = Subject.objects.create(school=self.school_a, name='Agriculture')
        self.auth('ac_admin_a')
        response = self.client.delete(f'/api/academics/subjects/{subject_a.id}/')
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Subject.objects.filter(id=subject_a.id).exists())

    def test_school_admin_cannot_delete_other_school_subject(self):
        subject_b = Subject.objects.create(school=self.school_b, name='Commerce')
        self.auth('ac_admin_a')
        response = self.client.delete(f'/api/academics/subjects/{subject_b.id}/')
        self.assertEqual(response.status_code, 404)

class TopicTests(AcademicsTestBase):
    def setUp(self):
        super().setUp()
        self.subject_a = Subject.objects.create(school=self.school_a, name='Mathematics')
        self.subject_b = Subject.objects.create(school=self.school_b, name='Mathematics')

    def test_school_admin_can_create_topic_for_own_subject(self):
        self.auth('ac_admin_a')
        response = self.client.post('/api/academics/topics/', {
            'subject': self.subject_a.id, 'class_level': self.form_1.id, 'title': 'Algebra',
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['school'], self.school_a.id)

    def test_school_admin_cannot_create_topic_referencing_other_school_subject(self):
        """Relationship validation: even though `school` gets forced to the
        admin's own school, the `subject` FK must also belong to that same
        school — otherwise the topic would point cross-tenant."""
        self.auth('ac_admin_a')
        response = self.client.post('/api/academics/topics/', {
            'subject': self.subject_b.id, 'class_level': self.form_1.id, 'title': 'Algebra',
        })
        self.assertEqual(response.status_code, 400)

    def test_system_admin_create_topic_with_mismatched_school_and_subject_fails(self):
        self.auth('ac_sysadmin')
        response = self.client.post('/api/academics/topics/', {
            'school': self.school_a.id, 'subject': self.subject_b.id,
            'class_level': self.form_1.id, 'title': 'Algebra',
        })
        self.assertEqual(response.status_code, 400)

    def test_system_admin_create_topic_with_matching_school_succeeds(self):
        self.auth('ac_sysadmin')
        response = self.client.post('/api/academics/topics/', {
            'school': self.school_b.id, 'subject': self.subject_b.id,
            'class_level': self.form_1.id, 'title': 'Algebra',
        })
        self.assertEqual(response.status_code, 201)

    def test_duplicate_topic_title_same_subject_level_rejected(self):
        Topic.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_1, title='Algebra',
        )
        self.auth('ac_admin_a')
        response = self.client.post('/api/academics/topics/', {
            'subject': self.subject_a.id, 'class_level': self.form_1.id, 'title': 'Algebra',
        })
        self.assertEqual(response.status_code, 400)

    def test_teacher_cannot_retrieve_other_school_topic(self):
        topic_b = Topic.objects.create(
            school=self.school_b, subject=self.subject_b, class_level=self.form_1, title='Algebra',
        )
        self.auth('ac_teacher_a')
        response = self.client.get(f'/api/academics/topics/{topic_b.id}/')
        self.assertEqual(response.status_code, 404)

    def test_school_admin_cannot_delete_other_school_topic(self):
        topic_b = Topic.objects.create(
            school=self.school_b, subject=self.subject_b, class_level=self.form_1, title='Algebra',
        )
        self.auth('ac_admin_a')
        response = self.client.delete(f'/api/academics/topics/{topic_b.id}/')
        self.assertEqual(response.status_code, 404)

class LearningObjectiveTests(AcademicsTestBase):
    def setUp(self):
        super().setUp()
        self.subject_a = Subject.objects.create(school=self.school_a, name='Mathematics')
        self.subject_b = Subject.objects.create(school=self.school_b, name='Mathematics')
        self.topic_a = Topic.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_1, title='Algebra',
        )
        self.topic_b = Topic.objects.create(
            school=self.school_b, subject=self.subject_b, class_level=self.form_1, title='Algebra',
        )

    def test_school_admin_can_create_learning_objective_for_own_topic(self):
        self.auth('ac_admin_a')
        response = self.client.post('/api/academics/learning-objectives/', {
            'topic': self.topic_a.id, 'description': 'Solve linear equations',
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['school'], self.school_a.id)

    def test_school_admin_cannot_create_learning_objective_referencing_other_school_topic(self):
        self.auth('ac_admin_a')
        response = self.client.post('/api/academics/learning-objectives/', {
            'topic': self.topic_b.id, 'description': 'Solve linear equations',
        })
        self.assertEqual(response.status_code, 400)

    def test_teacher_cannot_retrieve_other_school_learning_objective(self):
        objective_b = LearningObjective.objects.create(
            school=self.school_b, topic=self.topic_b, description='Solve linear equations',
        )
        self.auth('ac_teacher_a')
        response = self.client.get(f'/api/academics/learning-objectives/{objective_b.id}/')
        self.assertEqual(response.status_code, 404)

    def test_student_can_read_own_school_learning_objectives(self):
        LearningObjective.objects.create(
            school=self.school_a, topic=self.topic_a, description='Solve linear equations',
        )
        self.auth('ac_student_a')
        response = self.client.get('/api/academics/learning-objectives/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['count'], 1)

    def test_student_cannot_create_learning_objective(self):
        self.auth('ac_student_a')
        response = self.client.post('/api/academics/learning-objectives/', {
            'topic': self.topic_a.id, 'description': 'Should not work',
        })
        self.assertEqual(response.status_code, 403)
