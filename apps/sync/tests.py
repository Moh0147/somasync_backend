import uuid

from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import ClassLevel, Subject
from apps.accounts.models import User
from apps.classes.models import SchoolClass, StudentClassMembership
from apps.lessons.models import Lesson, LessonAssignment
from apps.quizzes.models import Question, QuestionOption, Quiz, QuizAssignment, StudentQuizAttempt
from apps.schools.models import School

from .models import SyncRecord


def create_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    username = username or f'{role.lower()}-{school.code if school else "sys"}-{admin_scope or ""}'
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user


def uid():
    return str(uuid.uuid4())


class SyncTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name='School A', code='SY-A')
        self.school_b = School.objects.create(name='School B', code='SY-B')

        self.teacher_a = create_user(User.Role.TEACHER, school=self.school_a, username='sy_teacher_a')
        self.student_a = create_user(User.Role.STUDENT, school=self.school_a, username='sy_student_a')
        self.student_b = create_user(User.Role.STUDENT, school=self.school_b, username='sy_student_b')

        self.form_2 = ClassLevel.objects.get(code='F2')
        self.subject_a = Subject.objects.create(school=self.school_a, name='Mathematics')
        self.class_a = SchoolClass.objects.create(
            school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2026,
        )
        StudentClassMembership.objects.create(school_class=self.class_a, student=self.student_a)

        self.quiz = Quiz.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2,
            title='Algebra Quiz', created_by=self.teacher_a, status=Quiz.Status.PUBLISHED,
        )
        self.question = Question.objects.create(
            school=self.school_a, quiz=self.quiz, created_by=self.teacher_a,
            question_type=Question.Type.MCQ, text='2+2=?', marks=2,
        )
        QuestionOption.objects.create(question=self.question, text='3', is_correct=False)
        self.correct_option = QuestionOption.objects.create(question=self.question, text='4', is_correct=True)
        QuizAssignment.objects.create(quiz=self.quiz, school_class=self.class_a, assigned_by=self.teacher_a)

        self.lesson = Lesson.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2,
            title='Intro to Algebra', created_by=self.teacher_a, status=Lesson.Status.PUBLISHED,
        )
        LessonAssignment.objects.create(lesson=self.lesson, school_class=self.class_a, assigned_by=self.teacher_a)

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

    def push(self, items):
        return self.client.post('/api/sync/push/', {'items': items}, format='json')

    def progress_item(self, client_id=None, **overrides):
        data = {'lesson': self.lesson.id, 'progress_percentage': 50}
        data.update(overrides)
        return {'entity_type': 'lesson_progress', 'client_id': client_id or uid(), 'data': data}

    def attempt_item(self, client_id=None, submitted=True, **overrides):
        data = {
            'quiz': self.quiz.id, 'submitted': submitted,
            'answers': [{'client_answer_id': uid(), 'question': self.question.id, 'selected_option': self.correct_option.id}],
        }
        data.update(overrides)
        return {'entity_type': 'quiz_attempt', 'client_id': client_id or uid(), 'data': data}


class PushFirstSyncTests(SyncTestBase):
    def setUp(self):
        super().setUp()
        self.auth('sy_student_a')

    def test_push_lesson_progress_creates_record(self):
        response = self.push([self.progress_item()])
        self.assertEqual(response.status_code, 200)
        results = response.data['data']['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'SYNCED')

    def test_push_quiz_attempt_creates_record(self):
        response = self.push([self.attempt_item()])
        results = response.data['data']['results']
        self.assertEqual(results[0]['status'], 'SYNCED')

    def test_push_creates_syncrecord_row(self):
        item = self.progress_item(client_id='fixed-id-1')
        self.push([item])
        record = SyncRecord.objects.get(user=self.student_a, entity_type='lesson_progress', client_id='fixed-id-1')
        self.assertEqual(record.status, SyncRecord.Status.SYNCED)
        self.assertEqual(record.attempts, 1)

    def test_push_batch_with_multiple_entity_types(self):
        response = self.push([self.progress_item(), self.attempt_item()])
        results = response.data['data']['results']
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['status'] == 'SYNCED' for r in results))

    def test_push_unknown_entity_type_fails_gracefully(self):
        response = self.push([{'entity_type': 'nonexistent_thing', 'client_id': uid(), 'data': {}}])
        results = response.data['data']['results']
        self.assertEqual(results[0]['status'], 'FAILED')
        self.assertIn('error', results[0])

    def test_teacher_cannot_use_push_endpoint(self):
        self.auth('sy_teacher_a')
        response = self.push([self.progress_item()])
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_push(self):
        self.client.credentials()
        response = self.push([self.progress_item()])
        self.assertEqual(response.status_code, 401)


class PushDuplicateAndRetryTests(SyncTestBase):
    def setUp(self):
        super().setUp()
        self.auth('sy_student_a')

    def test_pushing_same_item_twice_is_idempotent(self):
        item = self.progress_item(client_id='dup-1', progress_percentage=40)
        r1 = self.push([item])
        r2 = self.push([item])

        self.assertEqual(r1.data['data']['results'][0]['status'], 'SYNCED')
        self.assertEqual(r2.data['data']['results'][0]['status'], 'SYNCED')
        self.assertEqual(SyncRecord.objects.filter(client_id='dup-1').count(), 1)
        # The second push short-circuited -- attempts should NOT increment again.
        record = SyncRecord.objects.get(client_id='dup-1')
        self.assertEqual(record.attempts, 1)

    def test_duplicate_quiz_attempt_sync_does_not_duplicate_rows(self):
        item = self.attempt_item(client_id='dup-attempt-1')
        self.push([item])
        self.push([item])
        # The attempt's own client_attempt_id equals the sync client_id (see registry._push_quiz_attempt).
        self.assertEqual(StudentQuizAttempt.objects.filter(client_attempt_id='dup-attempt-1').count(), 1)

    def test_failed_push_can_be_retried_and_succeed(self):
        # First push references a lesson that doesn't exist -> FAILED.
        bad_item = self.progress_item(client_id='retry-1', lesson=999999)
        r1 = self.push([bad_item])
        self.assertEqual(r1.data['data']['results'][0]['status'], 'FAILED')

        # Retry with the SAME client_id but corrected data -> SYNCED.
        good_item = self.progress_item(client_id='retry-1')
        r2 = self.push([good_item])
        self.assertEqual(r2.data['data']['results'][0]['status'], 'SYNCED')

        record = SyncRecord.objects.get(client_id='retry-1')
        self.assertEqual(record.status, SyncRecord.Status.SYNCED)
        self.assertEqual(record.attempts, 2)
        self.assertEqual(record.error, '')

    def test_failed_item_does_not_block_other_items_in_batch(self):
        bad_item = {'entity_type': 'lesson_progress', 'client_id': uid(), 'data': {'lesson': 999999}}
        good_item = self.progress_item()
        response = self.push([bad_item, good_item])
        statuses = [r['status'] for r in response.data['data']['results']]
        self.assertEqual(statuses, ['FAILED', 'SYNCED'])

    def test_raised_db_error_mid_batch_does_not_poison_later_items(self):
        """Phase 21: the whole batch now commits as one outer transaction,
        with each item's handler call in its own savepoint. A handler that
        *raises* (not just returns success=False -- e.g. a genuine DB
        error) must roll back only its own savepoint, not the transaction
        the rest of the batch is relying on to still commit."""
        from unittest.mock import patch

        from django.db import IntegrityError

        from .registry import PUSH_HANDLERS

        exploding_item = self.progress_item()
        good_item = self.progress_item()
        real_handler = PUSH_HANDLERS['lesson_progress']

        def flaky_handler(user, client_id, data):
            if client_id == exploding_item['client_id']:
                raise IntegrityError('simulated DB error')
            return real_handler(user, client_id, data)

        with patch.dict(PUSH_HANDLERS, {'lesson_progress': flaky_handler}):
            response = self.push([exploding_item, good_item])

        self.assertEqual(response.status_code, 200)
        statuses = [r['status'] for r in response.data['data']['results']]
        self.assertEqual(statuses, ['FAILED', 'SYNCED'])
        self.assertIn('simulated DB error', response.data['data']['results'][0]['error'])

        # The second item's row must have actually committed, not just
        # been reflected in the in-memory response.
        record = SyncRecord.objects.get(client_id=good_item['client_id'])
        self.assertEqual(record.status, SyncRecord.Status.SYNCED)


class PushValidationAndIsolationTests(SyncTestBase):
    def setUp(self):
        super().setUp()
        self.auth('sy_student_a')

    def test_push_quiz_attempt_for_unpublished_quiz_fails(self):
        draft_quiz = Quiz.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2,
            title='Draft', created_by=self.teacher_a,
        )
        item = self.attempt_item(quiz=draft_quiz.id)
        response = self.push([item])
        self.assertEqual(response.data['data']['results'][0]['status'], 'FAILED')

    def test_push_cross_school_quiz_attempt_fails(self):
        self.auth('sy_student_b')
        item = self.attempt_item()  # references School A's quiz
        response = self.push([item])
        self.assertEqual(response.data['data']['results'][0]['status'], 'FAILED')
        self.assertFalse(StudentQuizAttempt.objects.filter(student=self.student_b).exists())

    def test_push_cross_school_lesson_progress_fails(self):
        self.auth('sy_student_b')
        item = self.progress_item()  # references School A's lesson
        response = self.push([item])
        self.assertEqual(response.data['data']['results'][0]['status'], 'FAILED')

    def test_sync_record_school_matches_pushing_student(self):
        item = self.progress_item(client_id='school-check-1')
        self.push([item])
        record = SyncRecord.objects.get(client_id='school-check-1')
        self.assertEqual(record.school_id, self.school_a.id)


class PullTests(SyncTestBase):
    def setUp(self):
        super().setUp()
        self.auth('sy_student_a')

    def test_pull_returns_pushed_lesson_progress(self):
        self.push([self.progress_item(progress_percentage=77)])
        response = self.client.get('/api/sync/pull/')
        self.assertEqual(response.status_code, 200)
        progress_items = response.data['data']['results']['lesson_progress']
        self.assertEqual(len(progress_items), 1)
        self.assertEqual(progress_items[0]['progress_percentage'], 77)

    def test_pull_returns_pushed_quiz_attempt(self):
        self.push([self.attempt_item()])
        response = self.client.get('/api/sync/pull/')
        attempts = response.data['data']['results']['quiz_attempt']
        self.assertEqual(len(attempts), 1)

    def test_pull_with_entity_type_filter_returns_only_that_type(self):
        self.push([self.progress_item(), self.attempt_item()])
        response = self.client.get('/api/sync/pull/?entity_type=lesson_progress')
        self.assertEqual(set(response.data['data']['results'].keys()), {'lesson_progress'})

    def test_pull_unknown_entity_type_rejected(self):
        response = self.client.get('/api/sync/pull/?entity_type=not_a_real_type')
        self.assertEqual(response.status_code, 400)

    def test_pull_invalid_since_format_rejected(self):
        response = self.client.get('/api/sync/pull/?since=not-a-timestamp')
        self.assertEqual(response.status_code, 400)

    def test_pull_since_excludes_older_items(self):
        self.push([self.progress_item()])
        future_timestamp = '2099-01-01T00:00:00Z'
        response = self.client.get(f'/api/sync/pull/?since={future_timestamp}')
        self.assertEqual(response.data['data']['results']['lesson_progress'], [])

    def test_pull_since_includes_items_updated_after_it(self):
        past_timestamp = '2000-01-01T00:00:00Z'
        self.push([self.progress_item()])
        response = self.client.get(f'/api/sync/pull/?since={past_timestamp}')
        self.assertEqual(len(response.data['data']['results']['lesson_progress']), 1)

    def test_pull_only_returns_own_data_not_other_students(self):
        self.push([self.progress_item()])

        other_student = create_user(User.Role.STUDENT, school=self.school_a, username='sy_student_a_other')
        StudentClassMembership.objects.create(school_class=self.class_a, student=other_student)
        self.auth('sy_student_a_other')
        response = self.client.get('/api/sync/pull/')
        self.assertEqual(response.data['data']['results']['lesson_progress'], [])

    def test_teacher_cannot_use_pull_endpoint(self):
        self.auth('sy_teacher_a')
        response = self.client.get('/api/sync/pull/')
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_pull(self):
        self.client.credentials()
        response = self.client.get('/api/sync/pull/')
        self.assertEqual(response.status_code, 401)
