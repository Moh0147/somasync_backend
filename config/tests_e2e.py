"""Phase 22: one capstone test walking the full realistic lifecycle across
every phase, end to end, over the real HTTP API (not the ORM directly,
except for the initial actor/roster setup that has no creation API by
design -- see Phase 20's audit note that user provisioning is
admin-panel-only, not an API surface).

This does not replace any app's own granular tests -- it exists
specifically to prove the SEAMS between phases still work together after
any future change: a lesson feeds progress tracking, a quiz's deterministic
mark feeds Explain My Mistake and analytics, an exam's AI-assisted mark
still requires an explicit teacher action before it counts, and a second
school never sees any of it. Each stage asserts only its own key milestone,
not every edge case already covered elsewhere.
"""
import json
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import ClassLevel, Subject
from apps.accounts.models import User
from apps.classes.models import SchoolClass, StudentClassMembership, TeacherClassMembership
from apps.exams.models import ExamQuestion, StudentExamAnswer, StudentExamAttempt
from apps.schools.models import School

def make_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user

class FullLifecycleTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.school = School.objects.create(name='Lifecycle School', code='E2E-A')
        self.other_school = School.objects.create(name='Other School', code='E2E-B')

        self.school_admin = make_user(
            User.Role.ADMIN, school=self.school, admin_scope=User.AdminScope.SCHOOL, username='e2e_admin',
        )
        self.teacher = make_user(User.Role.TEACHER, school=self.school, username='e2e_teacher')
        self.manager = make_user(User.Role.EXAM_MARKING_MANAGER, school=self.school, username='e2e_manager')
        self.student = make_user(User.Role.STUDENT, school=self.school, username='e2e_student')
        self.other_student = make_user(User.Role.STUDENT, school=self.other_school, username='e2e_other_student')

        self.form_2 = ClassLevel.objects.get(code='F2')
        self.subject = Subject.objects.create(school=self.school, name='Mathematics')
        self.school_class = SchoolClass.objects.create(
            school=self.school, class_level=self.form_2, name='Form II Blue', academic_year=2026,
        )
        TeacherClassMembership.objects.create(school_class=self.school_class, teacher=self.teacher)
        StudentClassMembership.objects.create(school_class=self.school_class, student=self.student)

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        self.assertEqual(login.status_code, 200, login.data)
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

    def test_full_lifecycle(self):
        self.auth('e2e_teacher')
        lesson_id = self.client.post('/api/lessons/', {
            'subject': self.subject.id, 'class_level': self.form_2.id,
            'title': 'Linear Equations', 'content': 'x + 1 = 2 -> x = 1',
        }).data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        assign_resp = self.client.post(f'/api/lessons/{lesson_id}/assign/', {'school_class': self.school_class.id})
        self.assertEqual(assign_resp.status_code, 201)

        quiz_id = self.client.post('/api/quizzes/', {
            'subject': self.subject.id, 'class_level': self.form_2.id, 'title': 'Algebra Quiz',
        }).data['data']['id']
        question_id = self.client.post('/api/quizzes/questions/', {
            'quiz': quiz_id, 'question_type': 'MCQ', 'text': '2 + 2 = ?', 'marks': 2, 'order': 1,
        }).data['data']['id']
        wrong_option_id = self.client.post(f'/api/quizzes/questions/{question_id}/options/', {
            'text': '5', 'is_correct': False, 'order': 1,
        }).data['data']['id']
        correct_option_id = self.client.post(f'/api/quizzes/questions/{question_id}/options/', {
            'text': '4', 'is_correct': True, 'order': 2,
        }).data['data']['id']
        self.client.post(f'/api/quizzes/{quiz_id}/publish/')
        self.client.post(f'/api/quizzes/{quiz_id}/assign/', {'school_class': self.school_class.id})

        self.auth('e2e_student')
        push_resp = self.client.post('/api/sync/push/', {'items': [
            {
                'entity_type': 'lesson_progress', 'client_id': 'e2e-progress-1',
                'data': {'lesson': lesson_id, 'progress_percentage': 100, 'completed': True},
            },
            {
                'entity_type': 'quiz_attempt', 'client_id': 'e2e-attempt-1',
                'data': {
                    'quiz': quiz_id, 'submitted': True,
                    'answers': [{
                        'client_answer_id': 'e2e-answer-1', 'question': question_id,
                        'selected_option': wrong_option_id,
                    }],
                },
            },
        ]}, format='json')
        self.assertEqual(push_resp.status_code, 200)
        statuses = {r['client_id']: r['status'] for r in push_resp.data['data']['results']}
        self.assertEqual(statuses['e2e-progress-1'], 'SYNCED')
        self.assertEqual(statuses['e2e-attempt-1'], 'SYNCED')

        from apps.progress.models import LessonProgress
        from apps.quizzes.models import StudentQuizAttempt, StudentQuizAnswer

        progress = LessonProgress.objects.get(student=self.student, lesson_id=lesson_id)
        self.assertTrue(progress.completed)

        attempt = StudentQuizAttempt.objects.get(student=self.student, quiz_id=quiz_id)
        self.assertEqual(attempt.percentage, 0)
        wrong_answer = StudentQuizAnswer.objects.get(attempt=attempt, question_id=question_id)
        self.assertFalse(wrong_answer.is_correct)

        with patch('apps.ai.explanation_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(return_value=Mock(text=json.dumps({
                'explanation': 'You added the numbers incorrectly.',
                'correct_concept': '2 + 2 = 4.',
                'concise_feedback': 'Recount carefully next time.',
            }))))
            explain_resp = self.client.post('/api/ai/explain-mistake/', {'student_answer': wrong_answer.id})
        self.assertEqual(explain_resp.status_code, 201)
        self.assertEqual(explain_resp.data['data']['status'], 'COMPLETED')

        wrong_answer.refresh_from_db()
        self.assertFalse(wrong_answer.is_correct)

        self.auth('e2e_teacher')
        exam_id = self.client.post('/api/exams/', {
            'subject': self.subject.id, 'class_level': self.form_2.id,
            'title': 'End of Term Exam', 'academic_year': 2026,
        }).data['data']['id']
        objective_q_id = self.client.post('/api/exams/questions/', {
            'exam': exam_id, 'question_number': '1', 'question_text': 'Capital of Tanzania?',
            'question_type': ExamQuestion.Type.OBJECTIVE, 'max_marks': 5, 'correct_answer': 'Dodoma',
        }).data['data']['id']
        self.client.post(f'/api/exams/questions/{objective_q_id}/marking-scheme/', {
            'criterion': 'Correct answer given', 'marks': 5,
        })
        subjective_q_id = self.client.post('/api/exams/questions/', {
            'exam': exam_id, 'question_number': '2', 'question_text': 'Explain the water cycle.',
            'question_type': ExamQuestion.Type.SUBJECTIVE, 'max_marks': 10,
        }).data['data']['id']
        self.client.post(f'/api/exams/questions/{subjective_q_id}/marking-scheme/', {
            'criterion': 'Explanation quality', 'marks': 10,
        })
        self.client.post(f'/api/exams/{exam_id}/publish/')
        self.client.post(f'/api/exams/{exam_id}/close/')
        self.client.post(f'/api/exams/{exam_id}/candidates/', {
            'student': self.student.id, 'candidate_number': 'F2A-E2E',
        })
        script_file = SimpleUploadedFile('script.pdf', b'%PDF-1.4 fake exam script', content_type='application/pdf')
        upload_resp = self.client.post(f'/api/exams/{exam_id}/scripts/', {
            'candidate_number': 'F2A-E2E', 'file': script_file,
        }, format='multipart')
        self.assertEqual(upload_resp.status_code, 201)

        script_id = self.client.get(f'/api/exams/{exam_id}/scripts/').data['data'][0]['id']
        attempt_ex = StudentExamAttempt.objects.get(exam_id=exam_id, student=self.student)
        objective_answer_id = StudentExamAnswer.objects.get(attempt=attempt_ex, exam_question_id=objective_q_id).id
        subjective_answer_id = StudentExamAnswer.objects.get(attempt=attempt_ex, exam_question_id=subjective_q_id).id

        self.client.post(f'/api/exams/{exam_id}/start-marking/')
        self.client.post(f'/api/exams/answers/{subjective_answer_id}/mark/', {
            'recorded_answer': 'Evaporation, condensation, then precipitation.',
        })

        with patch('apps.exams.ai_marking_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(return_value=Mock(text=json.dumps({
                'marks_awarded': 7, 'feedback': 'Missing the collection stage.',
            }))))
            ai_mark_resp = self.client.post(f'/api/exams/scripts/{script_id}/ai-mark/')
        self.assertEqual(ai_mark_resp.status_code, 200)

        subjective_answer = StudentExamAnswer.objects.get(id=subjective_answer_id)
        self.assertEqual(subjective_answer.ai_marks_awarded, 7)
        self.assertIsNone(subjective_answer.teacher_final_marks)

        self.client.post(f'/api/exams/answers/{objective_answer_id}/mark/', {'recorded_answer': 'Dodoma'})
        self.client.post(f'/api/exams/answers/{subjective_answer_id}/mark/', {'teacher_final_marks': 7})

        self.client.post(f'/api/exams/scripts/{script_id}/submit-for-review/')

        self.auth('e2e_manager')
        finalize_resp = self.client.post(f'/api/exams/scripts/{script_id}/finalize/')
        self.assertEqual(finalize_resp.status_code, 200)
        self.assertEqual(finalize_resp.data['data']['status'], 'FINALIZED')

        publish_resp = self.client.post(f'/api/exams/scripts/{script_id}/publish/')
        self.assertEqual(publish_resp.status_code, 200)

        self.auth('e2e_student')
        results = self.client.get('/api/exams/results/')
        self.assertEqual(results.data['data']['count'], 1)
        published_result = results.data['data']['results'][0]
        self.assertEqual(float(published_result['score']), 12)
        self.assertEqual(published_result['total_marks'], 15)

        self.auth('e2e_teacher')
        quiz_perf = self.client.get('/api/analytics/teacher/quizzes/').data['data']
        self.assertEqual(quiz_perf[0]['attempt_count'], 1)
        self.assertEqual(quiz_perf[0]['avg_percentage'], 0)

        exam_perf = self.client.get('/api/analytics/teacher/exams/').data['data']
        self.assertEqual(exam_perf[0]['candidate_count'], 1)
        self.assertAlmostEqual(exam_perf[0]['avg_percentage'], 12 / 15 * 100, places=2)

        lesson_perf = self.client.get('/api/analytics/teacher/lessons/').data['data']
        self.assertEqual(lesson_perf[0]['completed_count'], 1)

        self.auth('e2e_admin')
        overview = self.client.get('/api/analytics/school/overview/').data['data']
        self.assertEqual(overview['student_count'], 1)
        self.assertEqual(overview['teacher_count'], 1)

        self.auth('e2e_other_student')
        self.assertEqual(self.client.get('/api/lessons/').data['data']['count'], 0)
        self.assertEqual(self.client.get('/api/quizzes/').data['data']['count'], 0)
        self.assertEqual(self.client.get('/api/exams/results/').data['data']['count'], 0)
