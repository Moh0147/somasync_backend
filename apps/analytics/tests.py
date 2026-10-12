from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import ClassLevel, Subject
from apps.accounts.models import User
from apps.classes.models import SchoolClass, StudentClassMembership, TeacherClassMembership
from apps.exams.models import Exam, ExamCandidate, StudentExamAttempt
from apps.lessons.models import Lesson
from apps.progress.models import LessonProgress
from apps.quizzes.models import Question, QuestionOption, Quiz, StudentQuizAnswer, StudentQuizAttempt
from apps.schools.models import School

def create_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    username = username or f'{role.lower()}-{school.code if school else "sys"}-{admin_scope or ""}'
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user

class AnalyticsTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name='School A', code='AN-A')
        self.school_b = School.objects.create(name='School B', code='AN-B')

        self.sys_admin = create_user(User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, username='an_sysadmin')
        self.admin_a = create_user(
            User.Role.ADMIN, school=self.school_a, admin_scope=User.AdminScope.SCHOOL, username='an_admin_a',
        )
        self.teacher_a = create_user(User.Role.TEACHER, school=self.school_a, username='an_teacher_a')
        self.teacher_a2 = create_user(User.Role.TEACHER, school=self.school_a, username='an_teacher_a2')
        self.student_a = create_user(User.Role.STUDENT, school=self.school_a, username='an_student_a')
        self.student_a2 = create_user(User.Role.STUDENT, school=self.school_a, username='an_student_a2')

        self.form_2 = ClassLevel.objects.get(code='F2')
        self.subject_a = Subject.objects.create(school=self.school_a, name='Mathematics')

        self.class_a = SchoolClass.objects.create(
            school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2026,
        )
        TeacherClassMembership.objects.create(school_class=self.class_a, teacher=self.teacher_a)
        StudentClassMembership.objects.create(school_class=self.class_a, student=self.student_a)
        StudentClassMembership.objects.create(school_class=self.class_a, student=self.student_a2)

        self.quiz = Quiz.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2,
            title='Algebra Quiz', created_by=self.teacher_a, status=Quiz.Status.PUBLISHED,
        )
        self.question = Question.objects.create(
            school=self.school_a, quiz=self.quiz, created_by=self.teacher_a,
            question_type=Question.Type.MCQ, text='2+2=?', marks=2,
        )
        self.wrong_option = QuestionOption.objects.create(question=self.question, text='3', is_correct=False)
        self.correct_option = QuestionOption.objects.create(question=self.question, text='4', is_correct=True)

        attempt_a = StudentQuizAttempt.objects.create(
            school=self.school_a, quiz=self.quiz, student=self.student_a, client_attempt_id='an-attempt-a',
            status=StudentQuizAttempt.Status.SYNCED, score=2, total_marks=2, percentage=100,
        )
        StudentQuizAnswer.objects.create(
            attempt=attempt_a, question=self.question, client_answer_id='an-answer-a',
            selected_option=self.correct_option, is_correct=True, marks_awarded=2,
        )
        attempt_a2 = StudentQuizAttempt.objects.create(
            school=self.school_a, quiz=self.quiz, student=self.student_a2, client_attempt_id='an-attempt-a2',
            status=StudentQuizAttempt.Status.SYNCED, score=0, total_marks=2, percentage=0,
        )
        StudentQuizAnswer.objects.create(
            attempt=attempt_a2, question=self.question, client_answer_id='an-answer-a2',
            selected_option=self.wrong_option, is_correct=False, marks_awarded=0,
        )

        self.lesson = Lesson.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2,
            title='Intro to Algebra', created_by=self.teacher_a, status=Lesson.Status.PUBLISHED,
        )
        LessonProgress.objects.create(
            school=self.school_a, student=self.student_a, lesson=self.lesson,
            progress_percentage=100, completed=True,
        )
        LessonProgress.objects.create(
            school=self.school_a, student=self.student_a2, lesson=self.lesson,
            progress_percentage=40, completed=False,
        )

        self.exam = Exam.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2,
            title='Midterm Exam', academic_year=2026, created_by=self.teacher_a,
        )
        self.candidate_a = ExamCandidate.objects.create(
            exam=self.exam, student=self.student_a, candidate_number='F2A-001',
        )
        StudentExamAttempt.objects.create(
            school=self.school_a, exam=self.exam, candidate=self.candidate_a, student=self.student_a,
            score=8, total_marks=10, percentage=80,
        )

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

class TeacherAnalyticsTests(AnalyticsTestBase):
    def setUp(self):
        super().setUp()
        self.auth('an_teacher_a')

    def test_class_performance(self):
        response = self.client.get('/api/analytics/teacher/classes/')
        self.assertEqual(response.status_code, 200)
        rows = response.data['data']
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['class_id'], self.class_a.id)
        self.assertEqual(row['student_count'], 2)
        self.assertEqual(float(row['avg_quiz_percentage']), 50.0)

    def test_student_performance(self):
        response = self.client.get('/api/analytics/teacher/students/')
        self.assertEqual(response.status_code, 200)
        rows = {r['username']: r for r in response.data['data']}
        self.assertEqual(len(rows), 2)
        self.assertEqual(float(rows['an_student_a']['avg_quiz_percentage']), 100.0)
        self.assertEqual(float(rows['an_student_a2']['avg_quiz_percentage']), 0.0)
        self.assertEqual(rows['an_student_a']['lesson_completion_rate'], 100.0)
        self.assertEqual(rows['an_student_a2']['lesson_completion_rate'], 0.0)

    def test_student_performance_class_id_filter(self):
        response = self.client.get(f'/api/analytics/teacher/students/?class_id={self.class_a.id}')
        self.assertEqual(len(response.data['data']), 2)

    def test_student_performance_rejects_class_not_owned(self):
        other_class = SchoolClass.objects.create(
            school=self.school_a, name='Other Class', class_level=self.form_2, academic_year=2026,
        )
        response = self.client.get(f'/api/analytics/teacher/students/?class_id={other_class.id}')
        self.assertEqual(response.status_code, 400)

    def test_quiz_performance(self):
        response = self.client.get('/api/analytics/teacher/quizzes/')
        row = response.data['data'][0]
        self.assertEqual(row['quiz_id'], self.quiz.id)
        self.assertEqual(row['attempt_count'], 2)
        self.assertEqual(float(row['avg_percentage']), 50.0)

    def test_question_performance(self):
        response = self.client.get(f'/api/analytics/teacher/quizzes/{self.quiz.id}/questions/')
        row = response.data['data'][0]
        self.assertEqual(row['answered_count'], 2)
        self.assertEqual(row['correct_count'], 1)
        self.assertEqual(row['percent_correct'], 50.0)

    def test_question_performance_rejects_quiz_not_owned(self):
        self.auth('an_teacher_a2')
        response = self.client.get(f'/api/analytics/teacher/quizzes/{self.quiz.id}/questions/')
        self.assertEqual(response.status_code, 400)

    def test_lesson_completion(self):
        response = self.client.get('/api/analytics/teacher/lessons/')
        row = response.data['data'][0]
        self.assertEqual(row['lesson_id'], self.lesson.id)
        self.assertEqual(row['started_count'], 2)
        self.assertEqual(row['completed_count'], 1)
        self.assertEqual(row['completion_rate'], 50.0)

    def test_exam_performance(self):
        response = self.client.get('/api/analytics/teacher/exams/')
        row = response.data['data'][0]
        self.assertEqual(row['exam_id'], self.exam.id)
        self.assertEqual(row['candidate_count'], 1)
        self.assertEqual(float(row['avg_percentage']), 80.0)

    def test_other_teacher_sees_no_data_for_classes_they_dont_teach(self):
        self.auth('an_teacher_a2')
        response = self.client.get('/api/analytics/teacher/classes/')
        self.assertEqual(response.data['data'], [])
        response = self.client.get('/api/analytics/teacher/quizzes/')
        self.assertEqual(response.data['data'], [])

    def test_teacher_cannot_access_school_endpoints(self):
        response = self.client.get('/api/analytics/school/overview/')
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_access_system_endpoints(self):
        response = self.client.get('/api/analytics/system/schools/')
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_access_teacher_endpoints(self):
        self.auth('an_student_a')
        response = self.client.get('/api/analytics/teacher/classes/')
        self.assertEqual(response.status_code, 403)

class SchoolAdminAnalyticsTests(AnalyticsTestBase):
    def setUp(self):
        super().setUp()
        self.auth('an_admin_a')

    def test_school_overview(self):
        response = self.client.get('/api/analytics/school/overview/')
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertEqual(data['student_count'], 2)
        self.assertEqual(data['teacher_count'], 2)
        self.assertEqual(data['class_count'], 1)
        self.assertEqual(float(data['avg_quiz_percentage']), 50.0)
        self.assertEqual(float(data['avg_exam_percentage']), 80.0)
        self.assertEqual(data['lesson_completion_rate'], 50.0)

    def test_school_class_performance_includes_all_classes_regardless_of_teacher(self):
        other_class = SchoolClass.objects.create(
            school=self.school_a, name='Other Class', class_level=self.form_2, academic_year=2026,
        )
        response = self.client.get('/api/analytics/school/classes/')
        class_ids = {row['class_id'] for row in response.data['data']}
        self.assertEqual(class_ids, {self.class_a.id, other_class.id})

    def test_school_subject_performance(self):
        response = self.client.get('/api/analytics/school/subjects/')
        row = response.data['data'][0]
        self.assertEqual(row['subject_id'], self.subject_a.id)
        self.assertEqual(float(row['avg_quiz_percentage']), 50.0)
        self.assertEqual(float(row['avg_exam_percentage']), 80.0)

    def test_school_admin_isolated_from_other_school(self):
        subject_b = Subject.objects.create(school=self.school_b, name='Mathematics')
        admin_b = create_user(
            User.Role.ADMIN, school=self.school_b, admin_scope=User.AdminScope.SCHOOL, username='an_admin_b',
        )
        self.auth('an_admin_b')
        response = self.client.get('/api/analytics/school/overview/')
        self.assertEqual(response.data['data']['student_count'], 0)
        response = self.client.get('/api/analytics/school/subjects/')
        self.assertEqual(
            {row['subject_id'] for row in response.data['data']}, {subject_b.id},
        )

    def test_teacher_cannot_access_school_endpoints(self):
        self.auth('an_teacher_a')
        response = self.client.get('/api/analytics/school/overview/')
        self.assertEqual(response.status_code, 403)

    def test_system_admin_cannot_use_school_admin_endpoint(self):
        self.auth('an_sysadmin')
        response = self.client.get('/api/analytics/school/overview/')
        self.assertEqual(response.status_code, 403)

class SystemAdminAnalyticsTests(AnalyticsTestBase):
    def setUp(self):
        super().setUp()
        self.auth('an_sysadmin')

    def test_system_schools(self):
        response = self.client.get('/api/analytics/system/schools/')
        self.assertEqual(response.status_code, 200)
        rows = {row['school_id']: row for row in response.data['data']}
        self.assertEqual(rows[self.school_a.id]['student_count'], 2)
        self.assertEqual(rows[self.school_a.id]['teacher_count'], 2)
        self.assertEqual(rows[self.school_a.id]['class_count'], 1)
        self.assertIn(self.school_b.id, rows)

    def test_system_users(self):
        response = self.client.get('/api/analytics/system/users/')
        data = response.data['data']
        self.assertEqual(data['total_users'], User.objects.count())
        self.assertEqual(data['by_role']['STUDENT'], User.objects.filter(role='STUDENT').count())

    def test_system_usage(self):
        response = self.client.get('/api/analytics/system/usage/')
        data = response.data['data']
        self.assertEqual(data['school_count'], 2)
        self.assertEqual(data['quiz_count'], 1)
        self.assertEqual(data['quiz_attempt_count'], 2)
        self.assertEqual(data['exam_count'], 1)

    def test_school_admin_cannot_access_system_endpoints(self):
        self.auth('an_admin_a')
        response = self.client.get('/api/analytics/system/schools/')
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_access_system_endpoints(self):
        self.client.credentials()
        response = self.client.get('/api/analytics/system/usage/')
        self.assertEqual(response.status_code, 401)
