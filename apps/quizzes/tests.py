import uuid
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import ClassLevel, Subject
from apps.accounts.models import User
from apps.classes.models import SchoolClass, StudentClassMembership
from apps.schools.models import School

from .models import (
    MatchingPair, Question, QuestionOption, Quiz, QuizAssignment, StudentQuizAnswer, StudentQuizAttempt,
)
from .serializers import SafeQuestionSerializer
from .services import QuizMarkingService


def create_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    username = username or f'{role.lower()}-{school.code if school else "sys"}-{admin_scope or ""}'
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user


class QuizzesTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name='School A', code='QZ-A')
        self.school_b = School.objects.create(name='School B', code='QZ-B')

        self.sys_admin = create_user(User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, username='qz_sysadmin')
        self.admin_a = create_user(
            User.Role.ADMIN, school=self.school_a, admin_scope=User.AdminScope.SCHOOL, username='qz_admin_a',
        )
        self.teacher_a = create_user(User.Role.TEACHER, school=self.school_a, username='qz_teacher_a')
        self.teacher_a2 = create_user(User.Role.TEACHER, school=self.school_a, username='qz_teacher_a2')
        self.student_a = create_user(User.Role.STUDENT, school=self.school_a, username='qz_student_a')

        self.form_2 = ClassLevel.objects.get(code='F2')
        self.subject_a = Subject.objects.create(school=self.school_a, name='Mathematics')
        self.subject_b = Subject.objects.create(school=self.school_b, name='Mathematics')
        self.class_a = SchoolClass.objects.create(
            school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2026,
        )

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

    def create_quiz(self, **overrides):
        payload = {'subject': self.subject_a.id, 'class_level': self.form_2.id, 'title': 'Algebra Quiz'}
        payload.update(overrides)
        return self.client.post('/api/quizzes/', payload)

    def create_question(self, quiz_id, question_type, **overrides):
        payload = {'quiz': quiz_id, 'question_type': question_type, 'text': 'What is 2+2?'}
        payload.update(overrides)
        return self.client.post('/api/quizzes/questions/', payload)


class QuizPermissionTests(QuizzesTestBase):
    def test_teacher_can_create_quiz(self):
        self.auth('qz_teacher_a')
        response = self.create_quiz()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['status'], 'DRAFT')
        self.assertEqual(response.data['data']['created_by'], self.teacher_a.id)

    def test_student_cannot_create_quiz(self):
        self.auth('qz_student_a')
        response = self.create_quiz()
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_quiz_for_own_school(self):
        self.auth('qz_admin_a')
        response = self.create_quiz()
        self.assertEqual(response.status_code, 201)

    def test_teacher_cannot_create_quiz_with_other_school_subject(self):
        self.auth('qz_teacher_a')
        response = self.create_quiz(subject=self.subject_b.id)
        self.assertEqual(response.status_code, 400)

    def test_teacher_cannot_update_another_teachers_draft_quiz(self):
        self.auth('qz_teacher_a')
        quiz_id = self.create_quiz().data['data']['id']
        self.auth('qz_teacher_a2')
        response = self.client.patch(f'/api/quizzes/{quiz_id}/', {'title': 'Hijacked'})
        self.assertEqual(response.status_code, 404)  # invisible to a peer teacher

    def test_admin_can_edit_any_teachers_quiz(self):
        self.auth('qz_teacher_a')
        quiz_id = self.create_quiz().data['data']['id']
        self.auth('qz_admin_a')
        response = self.client.patch(f'/api/quizzes/{quiz_id}/', {'title': 'Admin edited'})
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_cannot_access_quizzes(self):
        response = self.client.get('/api/quizzes/')
        self.assertEqual(response.status_code, 401)


class MCQQuestionTests(QuizzesTestBase):
    def setUp(self):
        super().setUp()
        self.auth('qz_teacher_a')
        self.quiz_id = self.create_quiz().data['data']['id']

    def test_create_mcq_question(self):
        response = self.create_question(self.quiz_id, Question.Type.MCQ)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['question_type'], 'MCQ')
        self.assertEqual(response.data['data']['created_by'], self.teacher_a.id)

    def test_add_options_to_mcq_question(self):
        question_id = self.create_question(self.quiz_id, Question.Type.MCQ).data['data']['id']
        r1 = self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': '3', 'is_correct': False})
        r2 = self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': '4', 'is_correct': True})
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        listing = self.client.get(f'/api/quizzes/questions/{question_id}/options/')
        self.assertEqual(len(listing.data['data']), 2)

    def test_cannot_add_pairs_to_mcq_question(self):
        question_id = self.create_question(self.quiz_id, Question.Type.MCQ).data['data']['id']
        response = self.client.post(
            f'/api/quizzes/questions/{question_id}/pairs/', {'left_text': 'A', 'right_text': 'B'},
        )
        self.assertEqual(response.status_code, 400)

    def test_other_teacher_cannot_add_options(self):
        """Questions are authoring-only and scoped to the quiz's own
        editor — a peer teacher can't even see the question to attempt
        writing to it, so this 404s rather than 403s (same "don't confirm
        existence outside your visibility" pattern used throughout)."""
        question_id = self.create_question(self.quiz_id, Question.Type.MCQ).data['data']['id']
        self.auth('qz_teacher_a2')
        response = self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': '4', 'is_correct': True})
        self.assertEqual(response.status_code, 404)


class TrueFalseQuestionTests(QuizzesTestBase):
    def setUp(self):
        super().setUp()
        self.auth('qz_teacher_a')
        self.quiz_id = self.create_quiz().data['data']['id']
        self.question_id = self.create_question(
            self.quiz_id, Question.Type.TRUE_FALSE, text='2+2=4',
        ).data['data']['id']

    def test_add_exactly_two_options(self):
        r1 = self.client.post(f'/api/quizzes/questions/{self.question_id}/options/', {'text': 'True', 'is_correct': True})
        r2 = self.client.post(f'/api/quizzes/questions/{self.question_id}/options/', {'text': 'False', 'is_correct': False})
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)

    def test_cannot_add_a_third_option(self):
        self.client.post(f'/api/quizzes/questions/{self.question_id}/options/', {'text': 'True', 'is_correct': True})
        self.client.post(f'/api/quizzes/questions/{self.question_id}/options/', {'text': 'False', 'is_correct': False})
        response = self.client.post(
            f'/api/quizzes/questions/{self.question_id}/options/', {'text': 'Maybe', 'is_correct': False},
        )
        self.assertEqual(response.status_code, 400)


class MatchingQuestionTests(QuizzesTestBase):
    def setUp(self):
        super().setUp()
        self.auth('qz_teacher_a')
        self.quiz_id = self.create_quiz().data['data']['id']
        self.question_id = self.create_question(
            self.quiz_id, Question.Type.MATCHING, text='Match the capitals',
        ).data['data']['id']

    def test_add_matching_pairs(self):
        r1 = self.client.post(
            f'/api/quizzes/questions/{self.question_id}/pairs/', {'left_text': 'Tanzania', 'right_text': 'Dodoma'},
        )
        r2 = self.client.post(
            f'/api/quizzes/questions/{self.question_id}/pairs/', {'left_text': 'Kenya', 'right_text': 'Nairobi'},
        )
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)
        listing = self.client.get(f'/api/quizzes/questions/{self.question_id}/pairs/')
        self.assertEqual(len(listing.data['data']), 2)

    def test_cannot_add_options_to_matching_question(self):
        response = self.client.post(
            f'/api/quizzes/questions/{self.question_id}/options/', {'text': 'X', 'is_correct': True},
        )
        self.assertEqual(response.status_code, 400)

    def test_can_remove_a_pair(self):
        pair_id = self.client.post(
            f'/api/quizzes/questions/{self.question_id}/pairs/', {'left_text': 'A', 'right_text': 'B'},
        ).data['data']['id']
        response = self.client.delete(f'/api/quizzes/questions/{self.question_id}/pairs/', {'pair_id': pair_id})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MatchingPair.objects.filter(id=pair_id).exists())


class SafeQuestionSerializerTests(QuizzesTestBase):
    """SafeQuestionSerializer is the answer-hidden view used wherever a
    question needs to reach a student before they've answered (currently
    only section mini-quizzes, apps/lessons) -- QuestionSerializer itself
    stays authoring-only per QuestionViewSet's own docstring."""

    def setUp(self):
        super().setUp()
        self.auth('qz_teacher_a')
        self.quiz_id = self.create_quiz().data['data']['id']

    def test_mcq_options_omit_is_correct(self):
        question_id = self.create_question(self.quiz_id, Question.Type.MCQ, text='2+2=?').data['data']['id']
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': '4', 'is_correct': True})
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': '5', 'is_correct': False})

        question = Question.objects.prefetch_related('options').get(id=question_id)
        data = SafeQuestionSerializer(question).data
        self.assertTrue(all('is_correct' not in opt for opt in data['options']))
        self.assertEqual({opt['text'] for opt in data['options']}, {'4', '5'})

    def test_matching_right_text_never_exposed_only_shuffled_pool(self):
        question_id = self.create_question(self.quiz_id, Question.Type.MATCHING, text='Match capitals').data['data']['id']
        self.client.post(f'/api/quizzes/questions/{question_id}/pairs/', {'left_text': 'Tanzania', 'right_text': 'Dodoma'})
        self.client.post(f'/api/quizzes/questions/{question_id}/pairs/', {'left_text': 'Kenya', 'right_text': 'Nairobi'})

        question = Question.objects.prefetch_related('pairs').get(id=question_id)
        data = SafeQuestionSerializer(question).data
        self.assertTrue(all('right_text' not in pair for pair in data['pairs']))
        self.assertEqual(set(data['right_options']), {'Dodoma', 'Nairobi'})

    def test_explanation_not_included_in_safe_output(self):
        question_id = self.create_question(
            self.quiz_id, Question.Type.MCQ, text='2+2=?', explanation='Basic addition.',
        ).data['data']['id']
        question = Question.objects.get(id=question_id)
        data = SafeQuestionSerializer(question).data
        self.assertNotIn('explanation', data)


class QuizValidationTests(QuizzesTestBase):
    def setUp(self):
        super().setUp()
        self.auth('qz_teacher_a')
        self.quiz_id = self.create_quiz().data['data']['id']

    def test_cannot_publish_quiz_with_no_questions(self):
        response = self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_publish_mcq_with_no_correct_option(self):
        question_id = self.create_question(self.quiz_id, Question.Type.MCQ).data['data']['id']
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': 'A', 'is_correct': False})
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': 'B', 'is_correct': False})
        response = self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_publish_mcq_with_only_one_option(self):
        question_id = self.create_question(self.quiz_id, Question.Type.MCQ).data['data']['id']
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': 'A', 'is_correct': True})
        response = self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_publish_true_false_with_only_one_option(self):
        question_id = self.create_question(self.quiz_id, Question.Type.TRUE_FALSE).data['data']['id']
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': 'True', 'is_correct': True})
        response = self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_publish_matching_with_only_one_pair(self):
        question_id = self.create_question(self.quiz_id, Question.Type.MATCHING).data['data']['id']
        self.client.post(f'/api/quizzes/questions/{question_id}/pairs/', {'left_text': 'A', 'right_text': 'B'})
        response = self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_modify_question_of_published_quiz(self):
        question_id = self.create_question(self.quiz_id, Question.Type.MCQ).data['data']['id']
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': 'A', 'is_correct': False})
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': 'B', 'is_correct': True})
        self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')

        response = self.client.patch(f'/api/quizzes/questions/{question_id}/', {'text': 'Changed after publish'})
        self.assertEqual(response.status_code, 400)

    def test_cannot_add_question_to_published_quiz(self):
        question_id = self.create_question(self.quiz_id, Question.Type.MCQ).data['data']['id']
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': 'A', 'is_correct': False})
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': 'B', 'is_correct': True})
        self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')

        response = self.create_question(self.quiz_id, Question.Type.MCQ, text='New sneaky question')
        self.assertEqual(response.status_code, 400)


class QuizPublishingAndAssignmentTests(QuizzesTestBase):
    def setUp(self):
        super().setUp()
        self.auth('qz_teacher_a')
        self.quiz_id = self.create_quiz().data['data']['id']
        question_id = self.create_question(self.quiz_id, Question.Type.MCQ).data['data']['id']
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': 'A', 'is_correct': False})
        self.client.post(f'/api/quizzes/questions/{question_id}/options/', {'text': 'B', 'is_correct': True})

    def test_publish_a_well_formed_quiz(self):
        response = self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'PUBLISHED')
        self.assertIsNotNone(response.data['data']['published_at'])

    def test_cannot_publish_twice(self):
        self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        response = self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_assign_unpublished_quiz(self):
        response = self.client.post(f'/api/quizzes/{self.quiz_id}/assign/', {'school_class': self.class_a.id})
        self.assertEqual(response.status_code, 400)

    def test_assign_published_quiz_to_class(self):
        self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        response = self.client.post(f'/api/quizzes/{self.quiz_id}/assign/', {'school_class': self.class_a.id})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(QuizAssignment.objects.filter(quiz_id=self.quiz_id, school_class=self.class_a).exists())

    def test_cannot_assign_to_other_school_class(self):
        other_class = SchoolClass.objects.create(
            school=self.school_b, name='F2B', class_level=self.form_2, academic_year=2026,
        )
        self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        response = self.client.post(f'/api/quizzes/{self.quiz_id}/assign/', {'school_class': other_class.id})
        self.assertEqual(response.status_code, 400)

    def test_student_sees_published_quiz_but_not_its_questions(self):
        self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        self.auth('qz_student_a')
        quiz_response = self.client.get(f'/api/quizzes/{self.quiz_id}/')
        self.assertEqual(quiz_response.status_code, 200)

        questions_response = self.client.get(f'/api/quizzes/questions/?quiz={self.quiz_id}')
        self.assertEqual(questions_response.data['data']['count'], 0)

    def test_student_cannot_retrieve_question_directly(self):
        self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        question_id = Question.objects.filter(quiz_id=self.quiz_id).first().id
        self.auth('qz_student_a')
        response = self.client.get(f'/api/quizzes/questions/{question_id}/')
        self.assertEqual(response.status_code, 404)


class QuizAttemptTestBase(QuizzesTestBase):
    def setUp(self):
        super().setUp()
        self.school_b = self.school_b  # already created in QuizzesTestBase
        self.student_a2 = create_user(User.Role.STUDENT, school=self.school_a, username='qz_student_a2')
        self.student_b = create_user(User.Role.STUDENT, school=self.school_b, username='qz_student_b')
        self.class_b = SchoolClass.objects.create(
            school=self.school_b, name='F2B', class_level=self.form_2, academic_year=2026,
        )

        StudentClassMembership.objects.create(school_class=self.class_a, student=self.student_a)
        StudentClassMembership.objects.create(school_class=self.class_b, student=self.student_b)
        # student_a2 is deliberately NOT enrolled in class_a.

        self.auth('qz_teacher_a')
        self.quiz_id = self.create_quiz().data['data']['id']

        self.mcq_id = self.create_question(self.quiz_id, Question.Type.MCQ, text='2+2=?').data['data']['id']
        self.client.post(f'/api/quizzes/questions/{self.mcq_id}/options/', {'text': '3', 'is_correct': False})
        self.correct_option_id = self.client.post(
            f'/api/quizzes/questions/{self.mcq_id}/options/', {'text': '4', 'is_correct': True},
        ).data['data']['id']

        self.tf_id = self.create_question(self.quiz_id, Question.Type.TRUE_FALSE, text='Sky is blue').data['data']['id']
        self.client.post(f'/api/quizzes/questions/{self.tf_id}/options/', {'text': 'True', 'is_correct': True})
        self.client.post(f'/api/quizzes/questions/{self.tf_id}/options/', {'text': 'False', 'is_correct': False})

        self.client.post(f'/api/quizzes/{self.quiz_id}/publish/')
        self.client.post(f'/api/quizzes/{self.quiz_id}/assign/', {'school_class': self.class_a.id})

        self.auth('qz_student_a')

    def uid(self):
        return str(uuid.uuid4())


class AttemptCreationTests(QuizAttemptTestBase):
    def test_student_can_start_attempt(self):
        response = self.client.post('/api/quizzes/attempts/start/', {
            'quiz': self.quiz_id, 'client_attempt_id': self.uid(),
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['status'], 'IN_PROGRESS')
        self.assertEqual(response.data['data']['student'], self.student_a.id)

    def test_starting_twice_with_same_client_attempt_id_is_idempotent(self):
        client_attempt_id = self.uid()
        r1 = self.client.post('/api/quizzes/attempts/start/', {'quiz': self.quiz_id, 'client_attempt_id': client_attempt_id})
        r2 = self.client.post('/api/quizzes/attempts/start/', {'quiz': self.quiz_id, 'client_attempt_id': client_attempt_id})
        self.assertEqual(r1.data['data']['id'], r2.data['data']['id'])
        self.assertEqual(StudentQuizAttempt.objects.filter(client_attempt_id=client_attempt_id).count(), 1)

    def test_teacher_cannot_start_attempt(self):
        self.auth('qz_teacher_a')
        response = self.client.post('/api/quizzes/attempts/start/', {'quiz': self.quiz_id, 'client_attempt_id': self.uid()})
        self.assertEqual(response.status_code, 403)

    def test_cannot_start_attempt_for_unpublished_quiz(self):
        self.auth('qz_teacher_a')
        draft_quiz_id = self.create_quiz(title='Draft quiz').data['data']['id']
        self.auth('qz_student_a')
        response = self.client.post('/api/quizzes/attempts/start/', {'quiz': draft_quiz_id, 'client_attempt_id': self.uid()})
        self.assertEqual(response.status_code, 400)

    def test_cannot_start_attempt_for_unassigned_quiz(self):
        self.auth('qz_student_a2')  # enrolled in no class at all
        response = self.client.post('/api/quizzes/attempts/start/', {'quiz': self.quiz_id, 'client_attempt_id': self.uid()})
        self.assertEqual(response.status_code, 400)

    def test_student_from_other_school_cannot_start_attempt(self):
        self.auth('qz_student_b')
        response = self.client.post('/api/quizzes/attempts/start/', {'quiz': self.quiz_id, 'client_attempt_id': self.uid()})
        self.assertEqual(response.status_code, 400)


class AnswerSubmissionTests(QuizAttemptTestBase):
    def setUp(self):
        super().setUp()
        self.client_attempt_id = self.uid()
        self.client.post('/api/quizzes/attempts/start/', {'quiz': self.quiz_id, 'client_attempt_id': self.client_attempt_id})

    def test_answer_mcq_question(self):
        response = self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/answer/', {
            'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id,
        })
        self.assertEqual(response.status_code, 200)
        answers = response.data['data']['answers']
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]['selected_option'], self.correct_option_id)

    def test_re_answering_same_question_overwrites_not_duplicates(self):
        self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/answer/', {
            'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id,
        })
        wrong_option_id = QuestionOption.objects.filter(question_id=self.mcq_id, is_correct=False).first().id
        response = self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/answer/', {
            'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': wrong_option_id,
        })
        answers = response.data['data']['answers']
        self.assertEqual(len(answers), 1)
        self.assertEqual(answers[0]['selected_option'], wrong_option_id)

    def test_answer_matching_question(self):
        self.auth('qz_teacher_a')
        matching_quiz_id = self.create_quiz(title='Matching Quiz').data['data']['id']
        matching_q_id = self.create_question(
            matching_quiz_id, Question.Type.MATCHING, text='Match capitals',
        ).data['data']['id']
        pair1 = self.client.post(
            f'/api/quizzes/questions/{matching_q_id}/pairs/', {'left_text': 'Tanzania', 'right_text': 'Dodoma'},
        ).data['data']
        self.client.post(
            f'/api/quizzes/questions/{matching_q_id}/pairs/', {'left_text': 'Kenya', 'right_text': 'Nairobi'},
        )
        self.client.post(f'/api/quizzes/{matching_quiz_id}/publish/')
        self.client.post(f'/api/quizzes/{matching_quiz_id}/assign/', {'school_class': self.class_a.id})

        self.auth('qz_student_a')
        client_attempt_id = self.uid()
        self.client.post('/api/quizzes/attempts/start/', {'quiz': matching_quiz_id, 'client_attempt_id': client_attempt_id})
        response = self.client.post(f'/api/quizzes/attempts/{client_attempt_id}/answer/', {
            'client_answer_id': self.uid(), 'question': matching_q_id,
            'matching_response': {str(pair1['id']): 'Dodoma'},
        }, format='json')
        self.assertEqual(response.status_code, 200)
        answers = response.data['data']['answers']
        self.assertEqual(answers[0]['matching_response'], {str(pair1['id']): 'Dodoma'})

    def test_cannot_answer_someone_elses_attempt(self):
        self.auth('qz_student_a2')
        response = self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/answer/', {
            'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id,
        })
        self.assertEqual(response.status_code, 404)  # not in this student's scoped queryset

    def test_answer_referencing_question_from_other_quiz_rejected(self):
        self.auth('qz_teacher_a')
        other_quiz_id = self.create_quiz(title='Other quiz').data['data']['id']
        other_question_id = self.create_question(other_quiz_id, Question.Type.MCQ, text='Other?').data['data']['id']
        self.auth('qz_student_a')
        response = self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/answer/', {
            'client_answer_id': self.uid(), 'question': other_question_id, 'selected_option': self.correct_option_id,
        })
        self.assertEqual(response.status_code, 400)

    def test_cannot_answer_after_submit(self):
        self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/submit/')
        response = self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/answer/', {
            'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id,
        })
        self.assertEqual(response.status_code, 400)


class SubmitTests(QuizAttemptTestBase):
    def setUp(self):
        super().setUp()
        self.client_attempt_id = self.uid()
        self.client.post('/api/quizzes/attempts/start/', {'quiz': self.quiz_id, 'client_attempt_id': self.client_attempt_id})
        self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/answer/', {
            'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id,
        })

    def test_submit_sets_status_and_timestamp(self):
        response = self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/submit/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'SUBMITTED')
        self.assertIsNotNone(response.data['data']['submitted_at'])

    def test_submitting_twice_is_idempotent(self):
        self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/submit/')
        response = self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/submit/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(StudentQuizAttempt.objects.filter(client_attempt_id=self.client_attempt_id).count(), 1)

    def test_other_student_cannot_submit(self):
        self.auth('qz_student_a2')
        response = self.client.post(f'/api/quizzes/attempts/{self.client_attempt_id}/submit/')
        self.assertEqual(response.status_code, 404)


class OfflineSyncTests(QuizAttemptTestBase):
    def sync_payload(self, client_attempt_id, submitted=False, answers=None):
        return {
            'client_attempt_id': client_attempt_id,
            'quiz': self.quiz_id,
            'submitted': submitted,
            'answers': answers or [],
        }

    def test_sync_creates_attempt_with_answers(self):
        client_attempt_id = self.uid()
        payload = self.sync_payload(client_attempt_id, submitted=True, answers=[
            {'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id},
        ])
        response = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['status'], 'SYNCED')
        self.assertEqual(len(response.data['data']['answers']), 1)

    def test_syncing_same_attempt_twice_does_not_duplicate(self):
        """The exact scenario the spec calls out: the same offline attempt
        synced twice must not create duplicates."""
        client_attempt_id = self.uid()
        payload = self.sync_payload(client_attempt_id, submitted=True, answers=[
            {'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id},
        ])
        r1 = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')
        r2 = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')

        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.data['data']['id'], r2.data['data']['id'])
        self.assertEqual(StudentQuizAttempt.objects.filter(client_attempt_id=client_attempt_id).count(), 1)
        attempt = StudentQuizAttempt.objects.get(client_attempt_id=client_attempt_id)
        self.assertEqual(attempt.answers.count(), 1)

    def test_sync_not_submitted_leaves_in_progress(self):
        client_attempt_id = self.uid()
        payload = self.sync_payload(client_attempt_id, submitted=False, answers=[
            {'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id},
        ])
        response = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')
        self.assertEqual(response.data['data']['status'], 'IN_PROGRESS')

    def test_sync_with_invalid_answer_marks_failed_but_keeps_record(self):
        client_attempt_id = self.uid()
        payload = self.sync_payload(client_attempt_id, submitted=True, answers=[
            {'client_answer_id': self.uid(), 'question': 999999, 'selected_option': self.correct_option_id},
        ])
        response = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['data']['status'], 'FAILED')
        attempt = StudentQuizAttempt.objects.get(client_attempt_id=client_attempt_id)
        self.assertTrue(attempt.sync_error)

    def test_sync_from_other_school_rejected(self):
        self.auth('qz_student_b')
        payload = self.sync_payload(self.uid(), submitted=True)
        response = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')
        self.assertEqual(response.status_code, 400)

    def test_sync_for_unassigned_quiz_rejected(self):
        self.auth('qz_student_a2')
        payload = self.sync_payload(self.uid(), submitted=True)
        response = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')
        self.assertEqual(response.status_code, 400)

    def test_teacher_cannot_use_sync_endpoint(self):
        self.auth('qz_teacher_a')
        payload = self.sync_payload(self.uid(), submitted=True)
        response = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')
        self.assertEqual(response.status_code, 403)

    def test_live_start_then_offline_sync_same_client_attempt_id_updates_not_duplicates(self):
        client_attempt_id = self.uid()
        self.client.post('/api/quizzes/attempts/start/', {'quiz': self.quiz_id, 'client_attempt_id': client_attempt_id})
        payload = self.sync_payload(client_attempt_id, submitted=True, answers=[
            {'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id},
        ])
        response = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')
        self.assertEqual(response.status_code, 200)  # existing row updated, not created
        self.assertEqual(StudentQuizAttempt.objects.filter(client_attempt_id=client_attempt_id).count(), 1)
        self.assertEqual(response.data['data']['status'], 'SYNCED')


class AttemptOwnershipAndVisibilityTests(QuizAttemptTestBase):
    def setUp(self):
        super().setUp()
        self.client_attempt_id = self.uid()
        self.client.post('/api/quizzes/attempts/start/', {'quiz': self.quiz_id, 'client_attempt_id': self.client_attempt_id})

    def test_student_sees_only_own_attempts(self):
        self.auth('qz_student_a2')
        response = self.client.get('/api/quizzes/attempts/')
        self.assertEqual(response.data['data']['count'], 0)

    def test_student_sees_own_attempt(self):
        response = self.client.get('/api/quizzes/attempts/')
        self.assertEqual(response.data['data']['count'], 1)

    def test_teacher_sees_attempts_for_their_own_quiz(self):
        self.auth('qz_teacher_a')
        response = self.client.get('/api/quizzes/attempts/')
        self.assertEqual(response.data['data']['count'], 1)

    def test_admin_sees_attempts_in_their_school(self):
        self.auth('qz_admin_a')
        response = self.client.get('/api/quizzes/attempts/')
        self.assertEqual(response.data['data']['count'], 1)

    def test_other_school_student_cannot_retrieve_attempt_by_id(self):
        self.auth('qz_student_b')
        response = self.client.get(f'/api/quizzes/attempts/{self.client_attempt_id}/')
        self.assertEqual(response.status_code, 404)

    def test_unauthenticated_cannot_access_attempts(self):
        self.client.credentials()
        response = self.client.get('/api/quizzes/attempts/')
        self.assertEqual(response.status_code, 401)


class QuizMarkingServiceTests(QuizzesTestBase):
    """Direct unit tests of QuizMarkingService, built via the ORM for
    precise control over the answer key and submitted answers."""

    def setUp(self):
        super().setUp()
        self.quiz = Quiz.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2,
            title='Marking Quiz', created_by=self.teacher_a, status=Quiz.Status.PUBLISHED,
        )
        self.mcq = Question.objects.create(
            school=self.school_a, quiz=self.quiz, created_by=self.teacher_a,
            question_type=Question.Type.MCQ, text='2+2=?', marks=2,
        )
        self.mcq_wrong = QuestionOption.objects.create(question=self.mcq, text='3', is_correct=False)
        self.mcq_correct = QuestionOption.objects.create(question=self.mcq, text='4', is_correct=True)

        self.tf = Question.objects.create(
            school=self.school_a, quiz=self.quiz, created_by=self.teacher_a,
            question_type=Question.Type.TRUE_FALSE, text='Sky is blue', marks=1,
        )
        self.tf_true = QuestionOption.objects.create(question=self.tf, text='True', is_correct=True)
        self.tf_false = QuestionOption.objects.create(question=self.tf, text='False', is_correct=False)

        self.matching = Question.objects.create(
            school=self.school_a, quiz=self.quiz, created_by=self.teacher_a,
            question_type=Question.Type.MATCHING, text='Match capitals', marks=4,
        )
        self.pair1 = MatchingPair.objects.create(question=self.matching, left_text='Tanzania', right_text='Dodoma')
        self.pair2 = MatchingPair.objects.create(question=self.matching, left_text='Kenya', right_text='Nairobi')
        self.pair3 = MatchingPair.objects.create(question=self.matching, left_text='Uganda', right_text='Kampala')
        self.pair4 = MatchingPair.objects.create(question=self.matching, left_text='Rwanda', right_text='Kigali')

        self.attempt = StudentQuizAttempt.objects.create(
            school=self.school_a, quiz=self.quiz, student=self.student_a, client_attempt_id='marking-test-1',
        )

    def answer(self, question, selected_option=None, matching_response=None):
        return StudentQuizAnswer.objects.create(
            attempt=self.attempt, question=question, client_answer_id=f'ans-{question.id}',
            selected_option=selected_option, matching_response=matching_response or {},
        )

    def test_all_correct_gives_full_marks_and_100_percent(self):
        self.answer(self.mcq, selected_option=self.mcq_correct)
        self.answer(self.tf, selected_option=self.tf_true)
        self.answer(self.matching, matching_response={
            str(self.pair1.id): 'Dodoma', str(self.pair2.id): 'Nairobi',
            str(self.pair3.id): 'Kampala', str(self.pair4.id): 'Kigali',
        })

        QuizMarkingService.mark_attempt(self.attempt)
        self.attempt.refresh_from_db()

        self.assertEqual(self.attempt.total_marks, 7)
        self.assertEqual(self.attempt.score, Decimal('7.00'))
        self.assertEqual(self.attempt.percentage, Decimal('100.00'))

    def test_all_wrong_gives_zero(self):
        self.answer(self.mcq, selected_option=self.mcq_wrong)
        self.answer(self.tf, selected_option=self.tf_false)
        self.answer(self.matching, matching_response={
            str(self.pair1.id): 'Nairobi', str(self.pair2.id): 'Dodoma',
            str(self.pair3.id): 'Kigali', str(self.pair4.id): 'Kampala',
        })

        QuizMarkingService.mark_attempt(self.attempt)
        self.attempt.refresh_from_db()

        self.assertEqual(self.attempt.score, Decimal('0.00'))
        self.assertEqual(self.attempt.percentage, Decimal('0.00'))

    def test_mcq_correct_answer_marked_correct(self):
        answer = self.answer(self.mcq, selected_option=self.mcq_correct)
        QuizMarkingService.mark_attempt(self.attempt)
        answer.refresh_from_db()
        self.assertTrue(answer.is_correct)
        self.assertEqual(answer.marks_awarded, Decimal('2.00'))

    def test_true_false_wrong_answer_marked_incorrect(self):
        answer = self.answer(self.tf, selected_option=self.tf_false)
        QuizMarkingService.mark_attempt(self.attempt)
        answer.refresh_from_db()
        self.assertFalse(answer.is_correct)
        self.assertEqual(answer.marks_awarded, Decimal('0.00'))

    def test_partial_matching_gives_proportional_credit(self):
        # 2 of 4 pairs correct -> half of this question's 4 marks = 2.00
        answer = self.answer(self.matching, matching_response={
            str(self.pair1.id): 'Dodoma',   # correct
            str(self.pair2.id): 'Nairobi',  # correct
            str(self.pair3.id): 'Dodoma',   # wrong
            str(self.pair4.id): 'Nairobi',  # wrong
        })
        QuizMarkingService.mark_attempt(self.attempt)
        answer.refresh_from_db()
        self.assertEqual(answer.marks_awarded, Decimal('2.00'))
        self.assertFalse(answer.is_correct)  # not ALL pairs correct, so not fully correct

    def test_fully_correct_matching_marked_correct(self):
        answer = self.answer(self.matching, matching_response={
            str(self.pair1.id): 'Dodoma', str(self.pair2.id): 'Nairobi',
            str(self.pair3.id): 'Kampala', str(self.pair4.id): 'Kigali',
        })
        QuizMarkingService.mark_attempt(self.attempt)
        answer.refresh_from_db()
        self.assertTrue(answer.is_correct)
        self.assertEqual(answer.marks_awarded, Decimal('4.00'))

    def test_unanswered_question_scores_zero_but_still_counts_toward_total(self):
        self.answer(self.mcq, selected_option=self.mcq_correct)  # TF and MATCHING left unanswered
        QuizMarkingService.mark_attempt(self.attempt)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.score, Decimal('2.00'))
        self.assertEqual(self.attempt.total_marks, 7)

    def test_dangling_option_from_wrong_question_scores_zero_not_crash(self):
        """Defensive case: an option that doesn't belong to the question
        being answered (shouldn't happen via the API, but the service must
        not trust it blindly)."""
        answer = self.answer(self.mcq, selected_option=self.tf_true)  # option belongs to a DIFFERENT question
        QuizMarkingService.mark_attempt(self.attempt)
        answer.refresh_from_db()
        self.assertFalse(answer.is_correct)
        self.assertEqual(answer.marks_awarded, Decimal('0.00'))

    def test_missing_selected_option_scores_zero_not_crash(self):
        answer = self.answer(self.mcq, selected_option=None)
        QuizMarkingService.mark_attempt(self.attempt)
        answer.refresh_from_db()
        self.assertFalse(answer.is_correct)
        self.assertEqual(answer.marks_awarded, Decimal('0.00'))

    def test_matching_response_with_garbage_keys_scores_zero_not_crash(self):
        answer = self.answer(self.matching, matching_response={'not-a-real-pair-id': 'Dodoma', '99999': 'Nairobi'})
        QuizMarkingService.mark_attempt(self.attempt)
        answer.refresh_from_db()
        self.assertEqual(answer.marks_awarded, Decimal('0.00'))
        self.assertFalse(answer.is_correct)

    def test_total_marks_equals_sum_of_question_marks(self):
        QuizMarkingService.mark_attempt(self.attempt)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.total_marks, self.mcq.marks + self.tf.marks + self.matching.marks)

    def test_percentage_rounds_to_two_decimal_places(self):
        # Only the True/False (1 mark) correct out of 7 total -> 14.285714...%
        self.answer(self.tf, selected_option=self.tf_true)
        QuizMarkingService.mark_attempt(self.attempt)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.percentage, Decimal('14.29'))

    def test_marking_is_idempotent(self):
        self.answer(self.mcq, selected_option=self.mcq_correct)
        QuizMarkingService.mark_attempt(self.attempt)
        first_score = self.attempt.score
        QuizMarkingService.mark_attempt(self.attempt)
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.score, first_score)


class AutoMarkingIntegrationTests(QuizAttemptTestBase):
    """Confirms the API actually wires QuizMarkingService into submit/sync,
    not just that the service works in isolation."""

    def test_submit_triggers_marking(self):
        client_attempt_id = self.uid()
        self.client.post('/api/quizzes/attempts/start/', {'quiz': self.quiz_id, 'client_attempt_id': client_attempt_id})
        self.client.post(f'/api/quizzes/attempts/{client_attempt_id}/answer/', {
            'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id,
        })
        response = self.client.post(f'/api/quizzes/attempts/{client_attempt_id}/submit/')
        self.assertIsNotNone(response.data['data']['score'])
        self.assertIsNotNone(response.data['data']['total_marks'])
        self.assertIsNotNone(response.data['data']['percentage'])
        self.assertTrue(response.data['data']['answers'][0]['is_correct'])

    def test_sync_with_submitted_true_triggers_marking(self):
        client_attempt_id = self.uid()
        payload = {
            'client_attempt_id': client_attempt_id, 'quiz': self.quiz_id, 'submitted': True,
            'answers': [
                {'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id},
            ],
        }
        response = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')
        self.assertIsNotNone(response.data['data']['score'])
        self.assertTrue(response.data['data']['answers'][0]['is_correct'])

    def test_sync_without_submitted_does_not_mark(self):
        client_attempt_id = self.uid()
        payload = {
            'client_attempt_id': client_attempt_id, 'quiz': self.quiz_id, 'submitted': False,
            'answers': [
                {'client_answer_id': self.uid(), 'question': self.mcq_id, 'selected_option': self.correct_option_id},
            ],
        }
        response = self.client.post('/api/quizzes/attempts/sync/', payload, format='json')
        self.assertIsNone(response.data['data']['score'])
