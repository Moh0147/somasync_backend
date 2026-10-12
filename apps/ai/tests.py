import json
from unittest.mock import Mock, patch

import requests
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.academics.models import ClassLevel, Subject
from apps.accounts.models import User
from apps.classes.models import SchoolClass, StudentClassMembership
from apps.quizzes.models import (
    Question, QuestionOption, Quiz, QuizAssignment, StudentQuizAnswer, StudentQuizAttempt,
)
from apps.quizzes.services import QuizMarkingService
from apps.schools.models import School
from common.throttling import AIRequestThrottle

from .exceptions import AIServiceResponseError, AIServiceUnavailableError
from .models import AIExplanationRequest, MistakeExplanationCache
from .services import OllamaAIService, get_ai_service

def create_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    username = username or f'{role.lower()}-{school.code if school else "sys"}-{admin_scope or ""}'
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user

class AIServiceConfigurationTests(TestCase):
    def test_default_configuration_uses_settings(self):
        service = OllamaAIService()
        self.assertEqual(service.base_url, 'http://localhost:11434')
        self.assertEqual(service.model, 'qwen2.5:3b')
        self.assertEqual(service.timeout, 90)

    def test_explicit_overrides_take_precedence_over_settings(self):
        service = OllamaAIService(base_url='http://custom:9999/', model='llama3', timeout=5)
        self.assertEqual(service.base_url, 'http://custom:9999')
        self.assertEqual(service.model, 'llama3')
        self.assertEqual(service.timeout, 5)

    @override_settings(OLLAMA_MODEL='mistral', OLLAMA_BASE_URL='http://other:11434')
    def test_picks_up_changed_settings(self):
        service = OllamaAIService()
        self.assertEqual(service.model, 'mistral')
        self.assertEqual(service.base_url, 'http://other:11434')

    @override_settings(OPENAI_API_KEY='')
    def test_factory_returns_ollama_service_by_default(self):
        service = get_ai_service()
        self.assertIsInstance(service, OllamaAIService)

class AIServiceConnectionFailureTests(TestCase):
    def setUp(self):
        self.service = OllamaAIService(base_url='http://fake-ollama:11434', timeout=1)

    @patch('apps.ai.services.ollama.requests.post')
    def test_connection_error_raises_unavailable(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError('refused')
        with self.assertRaises(AIServiceUnavailableError):
            self.service.generate('Explain photosynthesis')

    @patch('apps.ai.services.ollama.requests.post')
    def test_timeout_raises_unavailable(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout('too slow')
        with self.assertRaises(AIServiceUnavailableError):
            self.service.generate('Explain photosynthesis')

    @patch('apps.ai.services.ollama.requests.post')
    def test_non_200_status_raises_unavailable(self, mock_post):
        mock_post.return_value = Mock(status_code=503)
        with self.assertRaises(AIServiceUnavailableError):
            self.service.generate('Explain photosynthesis')

    @patch('apps.ai.services.ollama.requests.post')
    def test_generic_request_exception_raises_unavailable(self, mock_post):
        mock_post.side_effect = requests.exceptions.RequestException('weird failure')
        with self.assertRaises(AIServiceUnavailableError):
            self.service.generate('Explain photosynthesis')

class AIServiceResponseValidationTests(TestCase):
    def setUp(self):
        self.service = OllamaAIService(base_url='http://fake-ollama:11434', timeout=1)

    @patch('apps.ai.services.ollama.requests.post')
    def test_valid_response_returns_text(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=lambda: {'response': '  Photosynthesis is...  '})
        result = self.service.generate('Explain photosynthesis')
        self.assertEqual(result.text, 'Photosynthesis is...')

    @patch('apps.ai.services.ollama.requests.post')
    def test_invalid_json_raises_response_error(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=Mock(side_effect=ValueError('not json')))
        with self.assertRaises(AIServiceResponseError):
            self.service.generate('Explain photosynthesis')

    @patch('apps.ai.services.ollama.requests.post')
    def test_error_field_in_response_raises_response_error(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=lambda: {'error': 'model not found'})
        with self.assertRaises(AIServiceResponseError):
            self.service.generate('Explain photosynthesis')

    @patch('apps.ai.services.ollama.requests.post')
    def test_missing_response_field_raises_response_error(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=lambda: {'done': True})
        with self.assertRaises(AIServiceResponseError):
            self.service.generate('Explain photosynthesis')

    @patch('apps.ai.services.ollama.requests.post')
    def test_empty_response_text_raises_response_error(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=lambda: {'response': '   '})
        with self.assertRaises(AIServiceResponseError):
            self.service.generate('Explain photosynthesis')

    @patch('apps.ai.services.ollama.requests.post')
    def test_non_dict_json_raises_response_error(self, mock_post):
        mock_post.return_value = Mock(status_code=200, json=lambda: ['not', 'a', 'dict'])
        with self.assertRaises(AIServiceResponseError):
            self.service.generate('Explain photosynthesis')

class AIServiceHealthCheckTests(TestCase):
    def setUp(self):
        self.service = OllamaAIService(base_url='http://fake-ollama:11434', timeout=1)

    @patch('apps.ai.services.ollama.requests.get')
    def test_health_check_true_when_reachable(self, mock_get):
        mock_get.return_value = Mock(status_code=200)
        self.assertTrue(self.service.health_check())

    @patch('apps.ai.services.ollama.requests.get')
    def test_health_check_false_on_connection_error(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError('refused')
        self.assertFalse(self.service.health_check())

    @patch('apps.ai.services.ollama.requests.get')
    def test_health_check_false_on_timeout(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout('slow')
        self.assertFalse(self.service.health_check())

    @patch('apps.ai.services.ollama.requests.get')
    def test_health_check_false_on_non_200(self, mock_get):
        mock_get.return_value = Mock(status_code=500)
        self.assertFalse(self.service.health_check())

    def test_health_check_never_raises(self):
        try:
            result = self.service.health_check()
        except Exception as exc:  # noqa: BLE001
            self.fail(f'health_check() raised {exc!r} instead of returning False')
        self.assertFalse(result)

class AIHealthEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = create_user(User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, username='ai_sysadmin')

    def auth(self, username='ai_sysadmin', password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

    def test_health_endpoint_requires_authentication(self):
        response = self.client.get('/api/ai/health/')
        self.assertEqual(response.status_code, 401)

    @patch('apps.ai.views.get_ai_service')
    def test_health_endpoint_reports_healthy(self, mock_get_service):
        mock_get_service.return_value = Mock(health_check=Mock(return_value=True))
        self.auth()
        response = self.client.get('/api/ai/health/')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['data']['healthy'])

    @patch('apps.ai.views.get_ai_service')
    def test_health_endpoint_reports_unhealthy(self, mock_get_service):
        mock_get_service.return_value = Mock(health_check=Mock(return_value=False))
        self.auth()
        response = self.client.get('/api/ai/health/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['data']['healthy'])

    def test_health_endpoint_never_exposes_ollama_details(self):
        """The endpoint must never leak the backend URL/model — that would
        be a step toward exposing Ollama directly to the frontend."""
        self.auth()
        response = self.client.get('/api/ai/health/')
        self.assertEqual(set(response.data['data'].keys()), {'healthy'})

def mock_ai_response(explanation='Because you swapped the signs.', correct_concept='Two negatives make a positive.', concise_feedback='Keep practicing!'):
    return Mock(text=json.dumps({
        'explanation': explanation, 'correct_concept': correct_concept, 'concise_feedback': concise_feedback,
    }))

class ExplainMistakeTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name='School A', code='AI-A')
        self.school_b = School.objects.create(name='School B', code='AI-B')

        self.teacher_a = create_user(User.Role.TEACHER, school=self.school_a, username='ai_teacher_a')
        self.admin_a = create_user(
            User.Role.ADMIN, school=self.school_a, admin_scope=User.AdminScope.SCHOOL, username='ai_admin_a',
        )
        self.student_a = create_user(User.Role.STUDENT, school=self.school_a, username='ai_student_a')
        self.student_a2 = create_user(User.Role.STUDENT, school=self.school_a, username='ai_student_a2')
        self.student_b = create_user(User.Role.STUDENT, school=self.school_b, username='ai_student_b')

        self.form_2 = ClassLevel.objects.get(code='F2')
        self.subject_a = Subject.objects.create(school=self.school_a, name='Mathematics')
        self.class_a = SchoolClass.objects.create(
            school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2026,
        )
        StudentClassMembership.objects.create(school_class=self.class_a, student=self.student_a)
        StudentClassMembership.objects.create(school_class=self.class_a, student=self.student_a2)

        self.quiz = Quiz.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2,
            title='Algebra Quiz', created_by=self.teacher_a, status=Quiz.Status.PUBLISHED,
        )
        QuizAssignment.objects.create(quiz=self.quiz, school_class=self.class_a, assigned_by=self.teacher_a)

        self.question_wrong = Question.objects.create(
            school=self.school_a, quiz=self.quiz, created_by=self.teacher_a,
            question_type=Question.Type.MCQ, text='2+2=?', marks=2,
        )
        self.wrong_option = QuestionOption.objects.create(question=self.question_wrong, text='3', is_correct=False)
        self.correct_option = QuestionOption.objects.create(question=self.question_wrong, text='4', is_correct=True)

        self.question_right = Question.objects.create(
            school=self.school_a, quiz=self.quiz, created_by=self.teacher_a,
            question_type=Question.Type.MCQ, text='3+3=?', marks=2,
        )
        QuestionOption.objects.create(question=self.question_right, text='5', is_correct=False)
        self.correct_option2 = QuestionOption.objects.create(question=self.question_right, text='6', is_correct=True)

        self.attempt = StudentQuizAttempt.objects.create(
            school=self.school_a, quiz=self.quiz, student=self.student_a, client_attempt_id='ai-attempt-1',
        )
        self.wrong_answer = StudentQuizAnswer.objects.create(
            attempt=self.attempt, question=self.question_wrong, client_answer_id='ai-answer-1',
            selected_option=self.wrong_option,
        )
        self.right_answer = StudentQuizAnswer.objects.create(
            attempt=self.attempt, question=self.question_right, client_answer_id='ai-answer-2',
            selected_option=self.correct_option2,
        )
        QuizMarkingService.mark_attempt(self.attempt)
        self.wrong_answer.refresh_from_db()
        self.right_answer.refresh_from_db()

        self.attempt2 = StudentQuizAttempt.objects.create(
            school=self.school_a, quiz=self.quiz, student=self.student_a2, client_attempt_id='ai-attempt-2',
        )
        self.wrong_answer2 = StudentQuizAnswer.objects.create(
            attempt=self.attempt2, question=self.question_wrong, client_answer_id='ai-answer-3',
            selected_option=self.wrong_option,
        )
        QuizMarkingService.mark_attempt(self.attempt2)
        self.wrong_answer2.refresh_from_db()

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

class SuccessfulExplanationTests(ExplainMistakeTestBase):
    def setUp(self):
        super().setUp()
        self.auth('ai_student_a')

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_student_can_request_explanation_for_wrong_answer(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(response.status_code, 201)
        data = response.data['data']
        self.assertEqual(data['status'], 'COMPLETED')
        self.assertEqual(data['explanation'], 'Because you swapped the signs.')
        self.assertEqual(data['correct_concept'], 'Two negatives make a positive.')
        self.assertEqual(data['concise_feedback'], 'Keep practicing!')

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_prompt_includes_real_question_and_answer_data(self, mock_get_service):
        service = Mock(generate=Mock(return_value=mock_ai_response()))
        mock_get_service.return_value = service
        self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})

        prompt = service.generate.call_args[0][0]
        self.assertIn('2+2=?', prompt)
        self.assertIn('4', prompt)
        self.assertIn('3', prompt)
        self.assertIn('Mathematics', prompt)

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_optional_context_is_forwarded_into_prompt(self, mock_get_service):
        service = Mock(generate=Mock(return_value=mock_ai_response()))
        mock_get_service.return_value = service
        self.client.post('/api/ai/explain-mistake/', {
            'student_answer': self.wrong_answer.id, 'context': 'I always mix up addition and subtraction',
        })
        prompt = service.generate.call_args[0][0]
        self.assertIn('I always mix up addition and subtraction', prompt)

    def test_cannot_request_explanation_for_correct_answer(self):
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.right_answer.id})
        self.assertEqual(response.status_code, 400)

    def test_cannot_request_explanation_for_unmarked_answer(self):
        fresh_attempt = StudentQuizAttempt.objects.create(
            school=self.school_a, quiz=self.quiz, student=self.student_a, client_attempt_id='ai-attempt-unmarked',
        )
        unmarked_answer = StudentQuizAnswer.objects.create(
            attempt=fresh_attempt, question=self.question_wrong, client_answer_id='ai-answer-unmarked',
            selected_option=self.wrong_option,
        )
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': unmarked_answer.id})
        self.assertEqual(response.status_code, 400)

class CachedExplanationTests(ExplainMistakeTestBase):
    @patch('apps.ai.explanation_service.get_ai_service')
    def test_second_students_identical_mistake_uses_cache(self, mock_get_service):
        service = Mock(generate=Mock(return_value=mock_ai_response()))
        mock_get_service.return_value = service

        self.auth('ai_student_a')
        r1 = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(service.generate.call_count, 1)

        self.auth('ai_student_a2')
        r2 = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer2.id})
        self.assertEqual(service.generate.call_count, 1)

        self.assertEqual(r1.data['data']['explanation'], r2.data['data']['explanation'])
        self.assertEqual(r2.data['data']['status'], 'COMPLETED')

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_cache_entry_created_after_first_generation(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
        self.auth('ai_student_a')
        self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(
            MistakeExplanationCache.objects.filter(question=self.question_wrong).count(), 1,
        )

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_cache_hit_count_increments(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
        self.auth('ai_student_a')
        self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.auth('ai_student_a2')
        self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer2.id})

        cache_entry = MistakeExplanationCache.objects.get(question=self.question_wrong)
        self.assertEqual(cache_entry.hit_count, 1)

class AIUnavailableTests(ExplainMistakeTestBase):
    def setUp(self):
        super().setUp()
        self.auth('ai_student_a')

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_ai_unavailable_marks_request_failed_not_hard_error(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(side_effect=AIServiceUnavailableError('down')))
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['status'], 'FAILED')
        self.assertTrue(response.data['data']['error'])

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_failed_request_does_not_get_cached(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(side_effect=AIServiceUnavailableError('down')))
        self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertFalse(MistakeExplanationCache.objects.filter(question=self.question_wrong).exists())

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_retry_after_failure_can_succeed(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(side_effect=AIServiceUnavailableError('down')))
        create_response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        request_id = create_response.data['data']['id']
        self.assertEqual(create_response.data['data']['status'], 'FAILED')

        mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
        retry_response = self.client.post(f'/api/ai/explain-mistake/{request_id}/retry/')
        self.assertEqual(retry_response.status_code, 200)
        self.assertEqual(retry_response.data['data']['status'], 'COMPLETED')

        record = AIExplanationRequest.objects.get(id=request_id)
        self.assertEqual(record.attempts, 2)

    def test_cannot_retry_a_completed_request(self):
        with patch('apps.ai.explanation_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
            create_response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        request_id = create_response.data['data']['id']
        response = self.client.post(f'/api/ai/explain-mistake/{request_id}/retry/')
        self.assertEqual(response.status_code, 400)

    def test_other_student_cannot_retry(self):
        with patch('apps.ai.explanation_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(side_effect=AIServiceUnavailableError('down')))
            create_response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        request_id = create_response.data['data']['id']

        self.auth('ai_student_a2')
        response = self.client.post(f'/api/ai/explain-mistake/{request_id}/retry/')
        self.assertEqual(response.status_code, 404)

class InvalidResponseTests(ExplainMistakeTestBase):
    def setUp(self):
        super().setUp()
        self.auth('ai_student_a')

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_non_json_response_marks_failed(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=Mock(text='This is not JSON at all.')))
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(response.data['data']['status'], 'FAILED')
        self.assertTrue(response.data['data']['error'])

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_missing_required_field_marks_failed(self, mock_get_service):
        bad_text = json.dumps({'explanation': 'Some text', 'correct_concept': 'Some concept'})
        mock_get_service.return_value = Mock(generate=Mock(return_value=Mock(text=bad_text)))
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(response.data['data']['status'], 'FAILED')

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_markdown_fenced_json_is_still_parsed(self, mock_get_service):
        payload = json.dumps({'explanation': 'X', 'correct_concept': 'Y', 'concise_feedback': 'Z'})
        fenced = f'```json\n{payload}\n```'
        mock_get_service.return_value = Mock(generate=Mock(return_value=Mock(text=fenced)))
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(response.data['data']['status'], 'COMPLETED')
        self.assertEqual(response.data['data']['explanation'], 'X')

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_invalid_response_does_not_get_cached(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=Mock(text='garbage')))
        self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertFalse(MistakeExplanationCache.objects.filter(question=self.question_wrong).exists())

class AuthorizationTests(ExplainMistakeTestBase):
    def test_unauthenticated_cannot_request_explanation(self):
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(response.status_code, 401)

    def test_teacher_cannot_request_explanation(self):
        self.auth('ai_teacher_a')
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_request_explanation_for_another_students_answer(self):
        self.auth('ai_student_a2')
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(response.status_code, 400)

    def test_student_cannot_list_another_students_requests(self):
        with patch('apps.ai.explanation_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
            self.auth('ai_student_a')
            self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})

        self.auth('ai_student_a2')
        response = self.client.get('/api/ai/explain-mistake/')
        self.assertEqual(response.data['data']['count'], 0)

    def test_teacher_can_view_requests_for_their_own_quiz(self):
        with patch('apps.ai.explanation_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
            self.auth('ai_student_a')
            self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})

        self.auth('ai_teacher_a')
        response = self.client.get('/api/ai/explain-mistake/')
        self.assertEqual(response.data['data']['count'], 1)

    def test_admin_can_view_requests_in_their_school(self):
        with patch('apps.ai.explanation_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
            self.auth('ai_student_a')
            self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})

        self.auth('ai_admin_a')
        response = self.client.get('/api/ai/explain-mistake/')
        self.assertEqual(response.data['data']['count'], 1)

class ContextLengthLimitTests(ExplainMistakeTestBase):
    """Phase 20: `context` is free text echoed straight into an LLM prompt
    -- it must be bounded so a client can't force an oversized prompt onto
    the shared local Ollama instance."""

    def setUp(self):
        super().setUp()
        self.auth('ai_student_a')

    @patch('apps.ai.explanation_service.get_ai_service')
    def test_context_within_limit_is_accepted(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
        response = self.client.post('/api/ai/explain-mistake/', {
            'student_answer': self.wrong_answer.id, 'context': 'x' * 1000,
        })
        self.assertEqual(response.status_code, 201)

    def test_context_over_limit_is_rejected(self):
        response = self.client.post('/api/ai/explain-mistake/', {
            'student_answer': self.wrong_answer.id, 'context': 'x' * 1001,
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.data['success'])

@override_settings(THROTTLING_ENABLED=True)
class AIRequestThrottleTests(ExplainMistakeTestBase):
    """THROTTLING_ENABLED is off in test mode (development.py) -- this
    class explicitly opts back in to verify the AI-endpoint throttle
    actually works, using its own tight 'ai_explain' rate (2/min).

    DRF's SimpleRateThrottle subclasses bind THROTTLE_RATES to
    api_settings.DEFAULT_THROTTLE_RATES once at import time, as a plain
    class attribute -- @override_settings on REST_FRAMEWORK does NOT
    re-read it. Mutating the dict in place (it's the same object) is what
    actually changes the effective rate for the duration of a test.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        super().setUp()
        self.auth('ai_student_a')

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()

    @patch.dict(AIRequestThrottle.THROTTLE_RATES, {'ai_explain': '2/min'})
    @patch('apps.ai.explanation_service.get_ai_service')
    def test_throttled_after_limit_exceeded(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
        self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        response = self.client.post('/api/ai/explain-mistake/', {'student_answer': self.wrong_answer.id})
        self.assertEqual(response.status_code, 429)
