import json
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import ClassLevel, LearningObjective, Subject, Topic
from apps.accounts.models import User
from apps.ai.exceptions import AIServiceResponseError, AIServiceUnavailableError
from apps.classes.models import SchoolClass, StudentClassMembership
from apps.schools.models import School

from .models import (
    Exam, ExamCandidate, ExamQuestion, ExamScript, ExamScriptAssignment,
    MarkingAuditLog, MarkingSchemeItem,
    StudentExamAnswer, StudentExamAttempt,
)

def create_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    username = username or f'{role.lower()}-{school.code if school else "sys"}-{admin_scope or ""}'
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user

class ExamsTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name='School A', code='EX-A')
        self.school_b = School.objects.create(name='School B', code='EX-B')

        self.sys_admin = create_user(User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, username='ex_sysadmin')
        self.admin_a = create_user(
            User.Role.ADMIN, school=self.school_a, admin_scope=User.AdminScope.SCHOOL, username='ex_admin_a',
        )
        self.teacher_a = create_user(User.Role.TEACHER, school=self.school_a, username='ex_teacher_a')
        self.teacher_a2 = create_user(User.Role.TEACHER, school=self.school_a, username='ex_teacher_a2')
        self.teacher_b = create_user(User.Role.TEACHER, school=self.school_b, username='ex_teacher_b')
        self.student_a = create_user(User.Role.STUDENT, school=self.school_a, username='ex_student_a')
        self.student_a2 = create_user(User.Role.STUDENT, school=self.school_a, username='ex_student_a2')
        self.student_b = create_user(User.Role.STUDENT, school=self.school_b, username='ex_student_b')

        self.form_2 = ClassLevel.objects.get(code='F2')
        self.subject_a = Subject.objects.create(school=self.school_a, name='Mathematics')
        self.subject_b = Subject.objects.create(school=self.school_b, name='Mathematics')
        self.topic_a = Topic.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2, title='Algebra',
        )
        self.objective_a = LearningObjective.objects.create(
            school=self.school_a, topic=self.topic_a, description='Solve linear equations',
        )
        other_subject_topic = Topic.objects.create(
            school=self.school_a, subject=Subject.objects.create(school=self.school_a, name='Biology'),
            class_level=self.form_2, title='Cells',
        )
        self.wrong_subject_objective = LearningObjective.objects.create(
            school=self.school_a, topic=other_subject_topic, description='Describe cell structure',
        )

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

    def create_exam(self, **overrides):
        payload = {
            'subject': self.subject_a.id, 'class_level': self.form_2.id,
            'title': 'Midterm Exam', 'academic_year': 2026,
        }
        payload.update(overrides)
        return self.client.post('/api/exams/', payload)

    def create_question(self, exam_id, question_type=ExamQuestion.Type.SUBJECTIVE, **overrides):
        payload = {
            'exam': exam_id, 'question_number': '1', 'question_text': 'Explain X.',
            'question_type': question_type, 'max_marks': 10,
        }
        payload.update(overrides)
        return self.client.post('/api/exams/questions/', payload)

class ExamPermissionTests(ExamsTestBase):
    def test_teacher_can_create_exam(self):
        self.auth('ex_teacher_a')
        response = self.create_exam()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['status'], 'DRAFT')
        self.assertEqual(response.data['data']['created_by'], self.teacher_a.id)

    def test_student_cannot_create_exam(self):
        self.auth('ex_student_a')
        response = self.create_exam()
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_exam_for_own_school(self):
        self.auth('ex_admin_a')
        response = self.create_exam()
        self.assertEqual(response.status_code, 201)

    def test_teacher_cannot_create_exam_with_other_school_subject(self):
        self.auth('ex_teacher_a')
        response = self.create_exam(subject=self.subject_b.id)
        self.assertEqual(response.status_code, 400)

    def test_duplicate_exam_title_same_school_year_rejected(self):
        self.auth('ex_teacher_a')
        self.create_exam()
        response = self.create_exam()
        self.assertEqual(response.status_code, 400)

    def test_same_title_different_year_allowed(self):
        self.auth('ex_teacher_a')
        self.create_exam(academic_year=2025)
        response = self.create_exam(academic_year=2026)
        self.assertEqual(response.status_code, 201)

    def test_school_admin_injected_school_id_ignored(self):
        self.auth('ex_teacher_a')
        response = self.create_exam(school=self.school_b.id)
        self.assertEqual(response.data['data']['school'], self.school_a.id)

    def test_teacher_cannot_update_another_teachers_draft_exam(self):
        self.auth('ex_teacher_a')
        exam_id = self.create_exam().data['data']['id']
        self.auth('ex_teacher_a2')
        response = self.client.patch(f'/api/exams/{exam_id}/', {'title': 'Hijacked'})
        self.assertEqual(response.status_code, 404)

    def test_admin_can_edit_any_teachers_exam(self):
        self.auth('ex_teacher_a')
        exam_id = self.create_exam().data['data']['id']
        self.auth('ex_admin_a')
        response = self.client.patch(f'/api/exams/{exam_id}/', {'title': 'Admin Edited Exam'})
        self.assertEqual(response.status_code, 200)

    def test_teacher_can_delete_own_draft_exam(self):
        self.auth('ex_teacher_a')
        exam_id = self.create_exam().data['data']['id']
        response = self.client.delete(f'/api/exams/{exam_id}/')
        self.assertEqual(response.status_code, 204)

    def test_teacher_cannot_list_other_school_exams(self):
        self.auth('ex_teacher_b')
        self.create_exam(subject=self.subject_b.id)
        self.auth('ex_teacher_a')
        response = self.client.get('/api/exams/')
        self.assertEqual(response.data['data']['count'], 0)

    def test_unauthenticated_cannot_access_exams(self):
        response = self.client.get('/api/exams/')
        self.assertEqual(response.status_code, 401)

class ExamQuestionTests(ExamsTestBase):
    def setUp(self):
        super().setUp()
        self.auth('ex_teacher_a')
        self.exam_id = self.create_exam().data['data']['id']

    def test_create_objective_question(self):
        response = self.create_question(self.exam_id, question_type=ExamQuestion.Type.OBJECTIVE, question_number='1')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['created_by'], self.teacher_a.id)

    def test_create_subjective_question_with_learning_objective(self):
        response = self.create_question(
            self.exam_id, question_number='2', learning_objective=self.objective_a.id,
        )
        self.assertEqual(response.status_code, 201)

    def test_learning_objective_must_match_exam_subject(self):
        response = self.create_question(
            self.exam_id, question_number='3', learning_objective=self.wrong_subject_objective.id,
        )
        self.assertEqual(response.status_code, 400)

    def test_question_number_unique_per_exam(self):
        self.create_question(self.exam_id, question_number='1')
        response = self.create_question(self.exam_id, question_number='1')
        self.assertEqual(response.status_code, 400)

    def test_other_teacher_cannot_add_question(self):
        """create doesn't go through get_object()/queryset filtering (it
        validates the `exam` FK directly in perform_create), so this is a
        403 ownership check, not a 404 visibility gate -- matches
        quizzes.QuestionViewSet's identical pattern."""
        self.auth('ex_teacher_a2')
        response = self.create_question(self.exam_id, question_number='1')
        self.assertEqual(response.status_code, 403)

    def test_student_cannot_create_question(self):
        self.auth('ex_student_a')
        response = self.create_question(self.exam_id, question_number='1')
        self.assertEqual(response.status_code, 403)

class MarkingSchemeTests(ExamsTestBase):
    def setUp(self):
        super().setUp()
        self.auth('ex_teacher_a')
        self.exam_id = self.create_exam().data['data']['id']
        self.question_id = self.create_question(
            self.exam_id, question_number='1', max_marks=10,
        ).data['data']['id']

    def test_add_marking_scheme_items_within_max_marks(self):
        r1 = self.client.post(
            f'/api/exams/questions/{self.question_id}/marking-scheme/',
            {'criterion': 'Correct formula', 'marks': 4},
        )
        r2 = self.client.post(
            f'/api/exams/questions/{self.question_id}/marking-scheme/',
            {'criterion': 'Correct working', 'marks': 6},
        )
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 201)

    def test_marking_scheme_sum_cannot_exceed_max_marks(self):
        self.client.post(
            f'/api/exams/questions/{self.question_id}/marking-scheme/', {'criterion': 'Part A', 'marks': 7},
        )
        response = self.client.post(
            f'/api/exams/questions/{self.question_id}/marking-scheme/', {'criterion': 'Part B', 'marks': 5},
        )
        self.assertEqual(response.status_code, 400)

    def test_can_remove_marking_scheme_item(self):
        item_id = self.client.post(
            f'/api/exams/questions/{self.question_id}/marking-scheme/', {'criterion': 'Part A', 'marks': 5},
        ).data['data']['id']
        response = self.client.delete(
            f'/api/exams/questions/{self.question_id}/marking-scheme/', {'item_id': item_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MarkingSchemeItem.objects.filter(id=item_id).exists())

    def test_other_teacher_cannot_add_marking_scheme_item(self):
        self.auth('ex_teacher_a2')
        response = self.client.post(
            f'/api/exams/questions/{self.question_id}/marking-scheme/', {'criterion': 'Sneaky', 'marks': 1},
        )
        self.assertEqual(response.status_code, 404)

class PublicationWorkflowTests(ExamsTestBase):
    def setUp(self):
        super().setUp()
        self.auth('ex_teacher_a')
        self.exam_id = self.create_exam().data['data']['id']

    def test_cannot_publish_exam_with_no_questions(self):
        response = self.client.post(f'/api/exams/{self.exam_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_publish_when_marking_scheme_incomplete(self):
        question_id = self.create_question(self.exam_id, question_number='1', max_marks=10).data['data']['id']
        self.client.post(
            f'/api/exams/questions/{question_id}/marking-scheme/', {'criterion': 'Part A', 'marks': 6},
        )
        response = self.client.post(f'/api/exams/{self.exam_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_can_publish_when_marking_scheme_matches_max_marks(self):
        question_id = self.create_question(self.exam_id, question_number='1', max_marks=10).data['data']['id']
        self.client.post(
            f'/api/exams/questions/{question_id}/marking-scheme/', {'criterion': 'Part A', 'marks': 4},
        )
        self.client.post(
            f'/api/exams/questions/{question_id}/marking-scheme/', {'criterion': 'Part B', 'marks': 6},
        )
        response = self.client.post(f'/api/exams/{self.exam_id}/publish/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'PUBLISHED')
        self.assertIsNotNone(response.data['data']['published_at'])

    def test_cannot_publish_twice(self):
        question_id = self.create_question(self.exam_id, question_number='1', max_marks=5).data['data']['id']
        self.client.post(f'/api/exams/questions/{question_id}/marking-scheme/', {'criterion': 'All', 'marks': 5})
        self.client.post(f'/api/exams/{self.exam_id}/publish/')
        response = self.client.post(f'/api/exams/{self.exam_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_modify_question_of_published_exam(self):
        question_id = self.create_question(self.exam_id, question_number='1', max_marks=5).data['data']['id']
        self.client.post(f'/api/exams/questions/{question_id}/marking-scheme/', {'criterion': 'All', 'marks': 5})
        self.client.post(f'/api/exams/{self.exam_id}/publish/')

        response = self.client.patch(f'/api/exams/questions/{question_id}/', {'question_text': 'Changed'})
        self.assertEqual(response.status_code, 400)

    def test_cannot_add_question_to_published_exam(self):
        question_id = self.create_question(self.exam_id, question_number='1', max_marks=5).data['data']['id']
        self.client.post(f'/api/exams/questions/{question_id}/marking-scheme/', {'criterion': 'All', 'marks': 5})
        self.client.post(f'/api/exams/{self.exam_id}/publish/')

        response = self.create_question(self.exam_id, question_number='2', max_marks=5)
        self.assertEqual(response.status_code, 400)

    def test_close_requires_published_status(self):
        response = self.client.post(f'/api/exams/{self.exam_id}/close/')
        self.assertEqual(response.status_code, 400)

    def test_publish_then_close_workflow(self):
        question_id = self.create_question(self.exam_id, question_number='1', max_marks=5).data['data']['id']
        self.client.post(f'/api/exams/questions/{question_id}/marking-scheme/', {'criterion': 'All', 'marks': 5})
        self.client.post(f'/api/exams/{self.exam_id}/publish/')
        response = self.client.post(f'/api/exams/{self.exam_id}/close/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'CLOSED')

    def test_cannot_delete_published_exam(self):
        question_id = self.create_question(self.exam_id, question_number='1', max_marks=5).data['data']['id']
        self.client.post(f'/api/exams/questions/{question_id}/marking-scheme/', {'criterion': 'All', 'marks': 5})
        self.client.post(f'/api/exams/{self.exam_id}/publish/')
        response = self.client.delete(f'/api/exams/{self.exam_id}/')
        self.assertEqual(response.status_code, 400)

class CandidateRosterTests(ExamsTestBase):
    def setUp(self):
        super().setUp()
        self.auth('ex_teacher_a')
        self.exam_id = self.create_exam().data['data']['id']

    def test_can_register_candidate(self):
        response = self.client.post(
            f'/api/exams/{self.exam_id}/candidates/', {'student': self.student_a.id, 'candidate_number': 'F2A-001'},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['candidate_number'], 'F2A-001')

    def test_candidate_number_unique_per_exam(self):
        self.client.post(
            f'/api/exams/{self.exam_id}/candidates/', {'student': self.student_a.id, 'candidate_number': 'F2A-001'},
        )
        response = self.client.post(
            f'/api/exams/{self.exam_id}/candidates/', {'student': self.student_a2.id, 'candidate_number': 'F2A-001'},
        )
        self.assertEqual(response.status_code, 400)

    def test_student_cannot_be_registered_twice(self):
        self.client.post(
            f'/api/exams/{self.exam_id}/candidates/', {'student': self.student_a.id, 'candidate_number': 'F2A-001'},
        )
        response = self.client.post(
            f'/api/exams/{self.exam_id}/candidates/', {'student': self.student_a.id, 'candidate_number': 'F2A-002'},
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_register_non_student_as_candidate(self):
        response = self.client.post(
            f'/api/exams/{self.exam_id}/candidates/',
            {'student': self.teacher_a2.id, 'candidate_number': 'F2A-001'},
        )
        self.assertEqual(response.status_code, 400)

    def test_cannot_register_student_from_other_school(self):
        response = self.client.post(
            f'/api/exams/{self.exam_id}/candidates/', {'student': self.student_b.id, 'candidate_number': 'F2A-001'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ExamCandidate.objects.filter(exam_id=self.exam_id).exists())

    def test_can_remove_candidate(self):
        self.client.post(
            f'/api/exams/{self.exam_id}/candidates/', {'student': self.student_a.id, 'candidate_number': 'F2A-001'},
        )
        response = self.client.delete(f'/api/exams/{self.exam_id}/candidates/', {'candidate_number': 'F2A-001'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ExamCandidate.objects.filter(exam_id=self.exam_id, candidate_number='F2A-001').exists())

    def test_other_teacher_cannot_manage_candidates(self):
        self.auth('ex_teacher_a2')
        response = self.client.post(
            f'/api/exams/{self.exam_id}/candidates/', {'student': self.student_a.id, 'candidate_number': 'F2A-001'},
        )
        self.assertEqual(response.status_code, 404)

    def test_admin_can_list_candidates(self):
        self.client.post(
            f'/api/exams/{self.exam_id}/candidates/', {'student': self.student_a.id, 'candidate_number': 'F2A-001'},
        )
        self.auth('ex_admin_a')
        response = self.client.get(f'/api/exams/{self.exam_id}/candidates/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['data']), 1)

class ExamScriptTestBase(ExamsTestBase):
    def setUp(self):
        super().setUp()
        self.auth('ex_teacher_a')
        self.exam_id = self.create_exam().data['data']['id']
        self.question_id = self.create_question(
            self.exam_id, question_number='1', max_marks=10,
        ).data['data']['id']
        self.client.post(
            f'/api/exams/questions/{self.question_id}/marking-scheme/', {'criterion': 'All', 'marks': 10},
        )
        self.client.post(f'/api/exams/{self.exam_id}/publish/')
        self.client.post(f'/api/exams/{self.exam_id}/close/')
        self.client.post(
            f'/api/exams/{self.exam_id}/candidates/',
            {'student': self.student_a.id, 'candidate_number': 'F2A-001'},
        )

    def pdf_file(self, name='script.pdf', content=b'%PDF-1.4 fake pdf content'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def upload_script(self, candidate_number='F2A-001', file=None):
        return self.client.post(f'/api/exams/{self.exam_id}/scripts/', {
            'candidate_number': candidate_number, 'file': file or self.pdf_file(),
        }, format='multipart')

class ScriptUploadTests(ExamScriptTestBase):
    def test_teacher_can_upload_script(self):
        response = self.upload_script()
        self.assertEqual(response.status_code, 201)
        data = response.data['data']
        self.assertEqual(data['status'], 'UPLOADED')
        self.assertEqual(data['version'], 1)
        self.assertTrue(data['checksum'])
        self.assertEqual(data['mime_type'], 'application/pdf')

    def test_upload_creates_attempt_and_placeholder_answers(self):
        self.upload_script()
        attempt = StudentExamAttempt.objects.get(exam_id=self.exam_id, student=self.student_a)
        self.assertEqual(attempt.answers.count(), 1)
        self.assertEqual(StudentExamAnswer.objects.filter(attempt=attempt, exam_question_id=self.question_id).count(), 1)

    def test_exam_school_candidate_student_all_consistent(self):
        self.upload_script()
        script = ExamScript.objects.get(exam_id=self.exam_id)
        self.assertEqual(script.school_id, self.school_a.id)
        self.assertEqual(script.student_id, self.student_a.id)
        self.assertEqual(script.candidate.student_id, self.student_a.id)
        self.assertEqual(script.candidate.exam_id, self.exam_id)

    def test_reupload_increments_version_and_keeps_history(self):
        first = self.upload_script(file=self.pdf_file('first.pdf'))
        second = self.upload_script(file=self.pdf_file('second.pdf'))
        self.assertEqual(first.data['data']['version'], 1)
        self.assertEqual(second.data['data']['version'], 2)
        self.assertEqual(ExamScript.objects.filter(exam_id=self.exam_id, candidate__candidate_number='F2A-001').count(), 2)

        attempt = StudentExamAttempt.objects.get(exam_id=self.exam_id, student=self.student_a)
        self.assertEqual(attempt.current_script.version, 2)

    def test_unregistered_candidate_number_rejected(self):
        response = self.upload_script(candidate_number='F2A-999')
        self.assertEqual(response.status_code, 400)

    def test_cannot_upload_before_exam_closed(self):
        self.auth('ex_teacher_a')
        other_exam_id = self.create_exam(title='Not Closed Exam').data['data']['id']
        response = self.client.post(f'/api/exams/{other_exam_id}/scripts/', {
            'candidate_number': 'F2A-001', 'file': self.pdf_file(),
        }, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_student_cannot_upload_script(self):
        """`scripts` is detail=True, so get_object() runs first -- and
        students have zero visibility into Exam objects at all (Phase
        14), so this 404s before the ownership check is ever reached."""
        self.auth('ex_student_a')
        response = self.upload_script()
        self.assertEqual(response.status_code, 404)

    def test_other_teacher_cannot_upload_script(self):
        self.auth('ex_teacher_a2')
        response = self.upload_script()
        self.assertEqual(response.status_code, 404)

    def test_admin_can_upload_script(self):
        self.auth('ex_admin_a')
        self.client.post(
            f'/api/exams/{self.exam_id}/candidates/',
            {'student': self.student_a2.id, 'candidate_number': 'F2A-002'},
        )
        response = self.upload_script(candidate_number='F2A-002')
        self.assertEqual(response.status_code, 201)

    def test_marking_manager_can_upload_script(self):
        create_user(User.Role.EXAM_MARKING_MANAGER, school=self.school_a, username='su_marking_mgr')
        self.auth('su_marking_mgr')
        response = self.upload_script()
        self.assertEqual(response.status_code, 201)

    def test_other_school_marking_manager_cannot_upload_script(self):
        create_user(User.Role.EXAM_MARKING_MANAGER, school=self.school_b, username='su_mgr_b')
        self.auth('su_mgr_b')
        response = self.upload_script()
        self.assertEqual(response.status_code, 404)

    def test_examiner_can_upload_script_and_is_auto_assigned(self):
        examiner = create_user(User.Role.EXAMINER, school=self.school_a, username='su_examiner')
        self.auth('ex_teacher_a')
        self.client.post(f'/api/exams/{self.exam_id}/start-marking/')
        self.auth('su_examiner')
        response = self.upload_script()
        self.assertEqual(response.status_code, 201)
        script_id = response.data['data']['id']
        self.assertTrue(
            ExamScriptAssignment.objects.filter(
                script_id=script_id, assignee=examiner, role='EXAMINER',
            ).exists(),
            'Examiner should be auto-assigned to a script they uploaded.',
        )

    def test_examiner_other_school_cannot_upload_script(self):
        create_user(User.Role.EXAMINER, school=self.school_b, username='su_examiner_b')
        self.auth('su_examiner_b')
        response = self.upload_script()
        self.assertEqual(response.status_code, 404)

class ScriptFileValidationTests(ExamScriptTestBase):
    def test_disallowed_extension_rejected(self):
        bad_file = SimpleUploadedFile('script.exe', b'MZ fake exe', content_type='application/octet-stream')
        response = self.upload_script(file=bad_file)
        self.assertEqual(response.status_code, 400)

    def test_oversized_file_rejected(self):
        big_content = b'a' * (26 * 1024 * 1024)
        big_file = SimpleUploadedFile('big.pdf', big_content, content_type='application/pdf')
        response = self.upload_script(file=big_file)
        self.assertEqual(response.status_code, 400)

    def test_image_upload_allowed(self):
        image_file = SimpleUploadedFile('script_page1.png', b'\x89PNG\r\n\x1a\n fake png bytes', content_type='image/png')
        response = self.upload_script(file=image_file)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['mime_type'], 'image/png')

    def test_missing_file_rejected(self):
        response = self.client.post(f'/api/exams/{self.exam_id}/scripts/', {'candidate_number': 'F2A-001'})
        self.assertEqual(response.status_code, 400)

    def test_checksum_is_server_computed_not_client_settable(self):
        file = SimpleUploadedFile('script.pdf', b'real content', content_type='application/pdf')
        response = self.client.post(f'/api/exams/{self.exam_id}/scripts/', {
            'candidate_number': 'F2A-001', 'file': file, 'checksum': 'totally-fake-checksum',
        }, format='multipart')
        self.assertNotEqual(response.data['data']['checksum'], 'totally-fake-checksum')

class ScriptCrossSchoolIsolationTests(ExamScriptTestBase):
    def test_candidate_number_from_a_different_exam_is_not_reused(self):
        self.auth('ex_teacher_b')
        exam_b_id = self.create_exam(title='School B Exam', subject=self.subject_b.id).data['data']['id']
        question_b_id = self.create_question(exam_b_id, question_number='1', max_marks=5).data['data']['id']
        self.client.post(f'/api/exams/questions/{question_b_id}/marking-scheme/', {'criterion': 'All', 'marks': 5})
        self.client.post(f'/api/exams/{exam_b_id}/publish/')
        self.client.post(f'/api/exams/{exam_b_id}/close/')

        response = self.client.post(f'/api/exams/{exam_b_id}/scripts/', {
            'candidate_number': 'F2A-001', 'file': self.pdf_file(),
        }, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_school_a_teacher_cannot_see_school_b_scripts(self):
        self.upload_script()
        self.auth('ex_teacher_b')
        exam_b_id = self.create_exam(title='School B Exam', subject=self.subject_b.id).data['data']['id']
        response = self.client.get(f'/api/exams/{exam_b_id}/scripts/')
        self.assertEqual(len(response.data['data']), 0)

    def test_unauthenticated_cannot_upload(self):
        self.client.credentials()
        response = self.client.post(f'/api/exams/{self.exam_id}/scripts/', {
            'candidate_number': 'F2A-001', 'file': self.pdf_file(),
        }, format='multipart')
        self.assertEqual(response.status_code, 401)

class ExamMarkingTestBase(ExamsTestBase):
    def setUp(self):
        super().setUp()
        self.auth('ex_teacher_a')
        self.exam_id = self.create_exam().data['data']['id']

        self.objective_q_id = self.create_question(
            self.exam_id, question_type=ExamQuestion.Type.OBJECTIVE, question_number='1',
            max_marks=5, correct_answer='B',
        ).data['data']['id']
        self.client.post(
            f'/api/exams/questions/{self.objective_q_id}/marking-scheme/',
            {'criterion': 'Correct option selected', 'marks': 5},
        )

        self.subjective_q_id = self.create_question(
            self.exam_id, question_type=ExamQuestion.Type.SUBJECTIVE, question_number='2', max_marks=10,
        ).data['data']['id']
        self.client.post(
            f'/api/exams/questions/{self.subjective_q_id}/marking-scheme/',
            {'criterion': 'Explanation quality', 'marks': 10},
        )

        self.client.post(f'/api/exams/{self.exam_id}/publish/')
        self.client.post(f'/api/exams/{self.exam_id}/close/')
        self.client.post(
            f'/api/exams/{self.exam_id}/candidates/',
            {'student': self.student_a.id, 'candidate_number': 'F2A-001'},
        )
        script_file = SimpleUploadedFile('script.pdf', b'%PDF-1.4 fake script', content_type='application/pdf')
        self.client.post(
            f'/api/exams/{self.exam_id}/scripts/', {'candidate_number': 'F2A-001', 'file': script_file},
            format='multipart',
        )

        attempt = StudentExamAttempt.objects.get(exam_id=self.exam_id, student=self.student_a)
        self.attempt_id = attempt.id
        self.objective_answer_id = StudentExamAnswer.objects.get(
            attempt=attempt, exam_question_id=self.objective_q_id,
        ).id
        self.subjective_answer_id = StudentExamAnswer.objects.get(
            attempt=attempt, exam_question_id=self.subjective_q_id,
        ).id

    def start_marking(self):
        return self.client.post(f'/api/exams/{self.exam_id}/start-marking/')

class ObjectiveMarkingTests(ExamMarkingTestBase):
    def setUp(self):
        super().setUp()
        self.start_marking()

    def test_recording_correct_answer_auto_marks_full_marks(self):
        response = self.client.post(
            f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'},
        )
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertTrue(data['is_correct'])
        self.assertEqual(float(data['teacher_final_marks']), 5.0)
        self.assertIsNotNone(data['marked_at'])

    def test_recording_wrong_answer_auto_marks_zero(self):
        response = self.client.post(
            f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'C'},
        )
        data = response.data['data']
        self.assertFalse(data['is_correct'])
        self.assertEqual(float(data['teacher_final_marks']), 0.0)

    def test_answer_matching_is_case_insensitive(self):
        response = self.client.post(
            f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'b'},
        )
        self.assertTrue(response.data['data']['is_correct'])

    def test_ai_marks_awarded_stays_null_for_objective(self):
        self.client.post(f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'})
        answer = StudentExamAnswer.objects.get(id=self.objective_answer_id)
        self.assertIsNone(answer.ai_marks_awarded)

    def test_teacher_can_override_objective_mark_explicitly(self):
        """Even a deterministic objective mark can be overridden by an
        explicit teacher_final_marks (e.g. the answer key itself was
        contested) -- passing marks directly skips auto-marking."""
        response = self.client.post(f'/api/exams/answers/{self.objective_answer_id}/mark/', {
            'recorded_answer': 'C', 'teacher_final_marks': 5,
        })
        self.assertEqual(float(response.data['data']['teacher_final_marks']), 5.0)

class SubjectiveMarkingTests(ExamMarkingTestBase):
    def setUp(self):
        super().setUp()
        self.start_marking()

    def test_subjective_answer_not_auto_marked(self):
        answer = StudentExamAnswer.objects.get(id=self.subjective_answer_id)
        self.assertIsNone(answer.teacher_final_marks)
        self.assertIsNone(answer.ai_marks_awarded)
        self.assertEqual(answer.ai_feedback, '')

    def test_teacher_can_manually_mark_subjective_answer(self):
        response = self.client.post(f'/api/exams/answers/{self.subjective_answer_id}/mark/', {
            'teacher_final_marks': 7, 'teacher_comment': 'Good explanation, missed one point.',
        })
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertEqual(float(data['teacher_final_marks']), 7.0)
        self.assertEqual(data['teacher_comment'], 'Good explanation, missed one point.')
        self.assertIsNotNone(data['marked_at'])

    def test_manual_marks_cannot_exceed_max_marks(self):
        response = self.client.post(
            f'/api/exams/answers/{self.subjective_answer_id}/mark/', {'teacher_final_marks': 11},
        )
        self.assertEqual(response.status_code, 400)

    def test_manual_marks_cannot_be_negative(self):
        response = self.client.post(
            f'/api/exams/answers/{self.subjective_answer_id}/mark/', {'teacher_final_marks': -1},
        )
        self.assertEqual(response.status_code, 400)

    def test_ai_marks_awarded_and_teacher_final_marks_are_independent(self):
        """Simulates what Phase 17 will do: an AI suggestion lands in
        ai_marks_awarded, completely separate from teacher_final_marks,
        which only a teacher's own action ever sets."""
        answer = StudentExamAnswer.objects.get(id=self.subjective_answer_id)
        answer.ai_marks_awarded = 6
        answer.ai_feedback = 'Mentions the key concept but lacks detail.'
        answer.save(update_fields=['ai_marks_awarded', 'ai_feedback'])

        response = self.client.get(f'/api/exams/answers/{self.subjective_answer_id}/')
        self.assertEqual(float(response.data['data']['ai_marks_awarded']), 6.0)
        self.assertIsNone(response.data['data']['teacher_final_marks'])

        self.client.post(f'/api/exams/answers/{self.subjective_answer_id}/mark/', {'teacher_final_marks': 8})
        answer.refresh_from_db()
        self.assertEqual(answer.ai_marks_awarded, 6)
        self.assertEqual(answer.teacher_final_marks, 8)

class MarkingModelValidationTests(ExamMarkingTestBase):
    def test_model_rejects_teacher_final_marks_above_max_marks(self):
        answer = StudentExamAnswer.objects.get(id=self.objective_answer_id)
        answer.teacher_final_marks = 999
        with self.assertRaises(ValidationError):
            answer.full_clean()

    def test_model_rejects_negative_ai_marks(self):
        answer = StudentExamAnswer.objects.get(id=self.subjective_answer_id)
        answer.ai_marks_awarded = -1
        with self.assertRaises(ValidationError):
            answer.full_clean()

    def test_objective_question_requires_correct_answer(self):
        self.auth('ex_teacher_a')
        response = self.create_question(
            self.exam_id, question_type=ExamQuestion.Type.OBJECTIVE, question_number='3',
            max_marks=5, correct_answer='',
        )
        self.assertEqual(response.status_code, 400)

class MarkingWorkflowTests(ExamMarkingTestBase):
    def test_cannot_mark_before_marking_status(self):
        response = self.client.post(
            f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'},
        )
        self.assertEqual(response.status_code, 400)

    def test_start_marking_requires_closed_status(self):
        self.start_marking()
        response = self.start_marking()
        self.assertEqual(response.status_code, 400)

    def test_marking_answers_does_not_change_exam_status(self):
        self.start_marking()
        self.client.post(f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'})
        exam = Exam.objects.get(id=self.exam_id)
        self.assertEqual(exam.status, Exam.Status.MARKING)

    def test_other_teacher_cannot_mark(self):
        self.start_marking()
        self.auth('ex_teacher_a2')
        response = self.client.post(
            f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'},
        )
        self.assertEqual(response.status_code, 404)

    def test_student_cannot_mark(self):
        self.start_marking()
        self.auth('ex_student_a')
        response = self.client.post(
            f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'},
        )
        self.assertEqual(response.status_code, 404)

    def test_admin_can_mark(self):
        self.start_marking()
        self.auth('ex_admin_a')
        response = self.client.post(
            f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'},
        )
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_cannot_mark(self):
        self.start_marking()
        self.client.credentials()
        response = self.client.post(
            f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'},
        )
        self.assertEqual(response.status_code, 401)

    def test_marking_manager_can_start_marking(self):
        mgr = create_user(User.Role.EXAM_MARKING_MANAGER, school=self.school_a, username='wf_mgr')
        self.auth('wf_mgr')
        response = self.client.post(f'/api/exams/{self.exam_id}/start-marking/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'MARKING')

    def test_other_school_marking_manager_cannot_start_marking(self):
        create_user(User.Role.EXAM_MARKING_MANAGER, school=self.school_b, username='wf_mgr_b')
        self.auth('wf_mgr_b')
        response = self.client.post(f'/api/exams/{self.exam_id}/start-marking/')
        self.assertEqual(response.status_code, 404)

    def test_peer_teacher_cannot_start_marking(self):
        self.auth('ex_teacher_a2')
        response = self.client.post(f'/api/exams/{self.exam_id}/start-marking/')
        self.assertEqual(response.status_code, 404)

def mock_ai_response(marks_awarded=8, feedback='Good answer, mostly complete.'):
    return Mock(text=json.dumps({'marks_awarded': marks_awarded, 'feedback': feedback}))

class AIMarkingTestBase(ExamMarkingTestBase):
    def setUp(self):
        super().setUp()
        self.script_id = ExamScript.objects.get(
            exam_id=self.exam_id, candidate__candidate_number='F2A-001',
        ).id
        self.start_marking()

    def pdf_file(self, name='script.pdf', content=b'%PDF-1.4 fake pdf content'):
        return SimpleUploadedFile(name, content, content_type='application/pdf')

    def record_subjective_answer(self, text='The mitochondria is the powerhouse of the cell.'):
        return self.client.post(f'/api/exams/answers/{self.subjective_answer_id}/mark/', {'recorded_answer': text})

    def ai_mark(self):
        return self.client.post(f'/api/exams/scripts/{self.script_id}/ai-mark/')

class AISubjectiveMarkingTests(AIMarkingTestBase):
    @patch('apps.exams.ai_marking_service.get_ai_service')
    def test_ai_mark_success_populates_ai_fields(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response(marks_awarded=8)))
        self.record_subjective_answer()
        response = self.ai_mark()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'MARKED')

        answer = StudentExamAnswer.objects.get(id=self.subjective_answer_id)
        self.assertEqual(answer.ai_marks_awarded, 8)
        self.assertEqual(answer.ai_feedback, 'Good answer, mostly complete.')
        self.assertIsNone(answer.teacher_final_marks)

    @patch('apps.exams.ai_marking_service.get_ai_service')
    def test_ai_mark_leaves_objective_answers_untouched(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
        self.record_subjective_answer()
        self.ai_mark()
        objective_answer = StudentExamAnswer.objects.get(id=self.objective_answer_id)
        self.assertIsNone(objective_answer.ai_marks_awarded)
        self.assertIsNone(objective_answer.teacher_final_marks)

    def test_ai_mark_skips_untranscribed_answers(self):
        response = self.ai_mark()
        self.assertEqual(response.status_code, 200)
        script = ExamScript.objects.get(id=self.script_id)
        self.assertEqual(script.status, 'MARKED')

    def test_ai_mark_requires_marking_status(self):
        self.auth('ex_teacher_a')
        other_exam_id = self.create_exam(title='Not Marking Exam').data['data']['id']
        q_id = self.create_question(
            other_exam_id, question_type=ExamQuestion.Type.SUBJECTIVE, question_number='1', max_marks=5,
        ).data['data']['id']
        self.client.post(f'/api/exams/questions/{q_id}/marking-scheme/', {'criterion': 'All', 'marks': 5})
        self.client.post(f'/api/exams/{other_exam_id}/publish/')
        self.client.post(f'/api/exams/{other_exam_id}/candidates/', {
            'student': self.student_a2.id, 'candidate_number': 'F2A-002',
        })
        response = self.client.post(f'/api/exams/{other_exam_id}/scripts/', {
            'candidate_number': 'F2A-002', 'file': self.pdf_file(),
        }, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_ai_mark_cannot_rerun_on_marked_script(self):
        with patch('apps.exams.ai_marking_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
            self.record_subjective_answer()
            self.ai_mark()
        response = self.ai_mark()
        self.assertEqual(response.status_code, 400)

class AIFailureAndRetryTests(AIMarkingTestBase):
    @patch('apps.exams.ai_marking_service.get_ai_service')
    def test_ai_unavailable_marks_script_failed(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(side_effect=AIServiceUnavailableError('down')))
        self.record_subjective_answer()
        response = self.ai_mark()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'FAILED')
        self.assertTrue(response.data['data']['marking_error'])

        answer = StudentExamAnswer.objects.get(id=self.subjective_answer_id)
        self.assertIsNone(answer.ai_marks_awarded)

    @patch('apps.exams.ai_marking_service.get_ai_service')
    def test_invalid_json_response_marks_script_failed(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=Mock(text='not json at all')))
        self.record_subjective_answer()
        response = self.ai_mark()
        self.assertEqual(response.data['data']['status'], 'FAILED')

    @patch('apps.exams.ai_marking_service.get_ai_service')
    def test_out_of_range_marks_rejected(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response(marks_awarded=50)))
        self.record_subjective_answer()
        response = self.ai_mark()
        self.assertEqual(response.data['data']['status'], 'FAILED')
        answer = StudentExamAnswer.objects.get(id=self.subjective_answer_id)
        self.assertIsNone(answer.ai_marks_awarded)

    @patch('apps.exams.ai_marking_service.get_ai_service')
    def test_missing_feedback_rejected(self, mock_get_service):
        mock_get_service.return_value = Mock(generate=Mock(return_value=Mock(text=json.dumps({'marks_awarded': 5}))))
        self.record_subjective_answer()
        response = self.ai_mark()
        self.assertEqual(response.data['data']['status'], 'FAILED')

    def test_retry_after_failure_can_succeed(self):
        self.record_subjective_answer()
        with patch('apps.exams.ai_marking_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(side_effect=AIServiceUnavailableError('down')))
            r1 = self.ai_mark()
        self.assertEqual(r1.data['data']['status'], 'FAILED')

        with patch('apps.exams.ai_marking_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response()))
            r2 = self.ai_mark()
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.data['data']['status'], 'MARKED')

    def test_manual_marking_works_even_when_ai_never_attempted(self):
        """The core resilience guarantee: a teacher can mark subjective
        answers entirely by hand, without ever calling ai-mark."""
        response = self.client.post(f'/api/exams/answers/{self.subjective_answer_id}/mark/', {
            'teacher_final_marks': 9, 'teacher_comment': 'Marked manually, AI not used.',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(float(response.data['data']['teacher_final_marks']), 9.0)

        script = ExamScript.objects.get(id=self.script_id)
        self.assertEqual(script.status, 'UPLOADED')

class ScriptWorkflowTests(AIMarkingTestBase):
    def setUp(self):
        super().setUp()
        self.marking_mgr_wf = create_user(
            User.Role.EXAM_MARKING_MANAGER, school=self.school_a, username='wf_marking_mgr',
        )

    def mark_everything_manually(self):
        self.client.post(f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'})
        self.client.post(
            f'/api/exams/answers/{self.subjective_answer_id}/mark/',
            {'teacher_final_marks': 8, 'teacher_comment': 'Solid answer.'},
        )

    def run_ai_mark_success(self):
        with patch('apps.exams.ai_marking_service.get_ai_service') as mock_get_service:
            mock_get_service.return_value = Mock(generate=Mock(return_value=mock_ai_response(marks_awarded=8)))
            self.record_subjective_answer()
            return self.ai_mark()

    def finalize_as_manager(self):
        """Finalize the script as EXAM_MARKING_MANAGER, then restore teacher auth."""
        self.auth('wf_marking_mgr')
        resp = self.client.post(f'/api/exams/scripts/{self.script_id}/finalize/')
        self.auth('ex_teacher_a')
        return resp

    def test_cannot_submit_for_review_before_marked(self):
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/submit-for-review/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_finalize_before_submit_for_review(self):
        self.run_ai_mark_success()
        self.auth('wf_marking_mgr')
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/finalize/')
        self.assertEqual(response.status_code, 400)

    def test_finalize_blocked_if_any_answer_unmarked(self):
        self.run_ai_mark_success()
        self.client.post(f'/api/exams/scripts/{self.script_id}/submit-for-review/')
        self.auth('wf_marking_mgr')
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/finalize/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('teacher_final_marks', response.data['errors'][0])

    def test_full_happy_path_ai_mark_review_finalize(self):
        self.run_ai_mark_success()
        self.client.post(f'/api/exams/scripts/{self.script_id}/submit-for-review/')

        self.client.post(f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'})
        self.client.post(f'/api/exams/answers/{self.subjective_answer_id}/mark/', {'teacher_final_marks': 8})

        response = self.finalize_as_manager()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'FINALIZED')

        attempt = StudentExamAttempt.objects.get(id=self.attempt_id)
        self.assertEqual(attempt.total_marks, 15)
        self.assertEqual(attempt.score, 13)
        self.assertAlmostEqual(float(attempt.percentage), 13 / 15 * 100, places=2)

    def test_finalize_allowed_with_manual_marks_only_no_ai(self):
        self.mark_everything_manually()
        script = ExamScript.objects.get(id=self.script_id)
        script.status = ExamScript.Status.MARKED
        script.save(update_fields=['status'])
        self.client.post(f'/api/exams/scripts/{self.script_id}/submit-for-review/')
        response = self.finalize_as_manager()
        self.assertEqual(response.status_code, 200)

    def test_finalized_status_cannot_be_finalized_again(self):
        self.mark_everything_manually()
        script = ExamScript.objects.get(id=self.script_id)
        script.status = ExamScript.Status.MARKED
        script.save(update_fields=['status'])
        self.client.post(f'/api/exams/scripts/{self.script_id}/submit-for-review/')
        self.finalize_as_manager()
        self.auth('wf_marking_mgr')
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/finalize/')
        self.assertEqual(response.status_code, 400)

class ScriptMarkingPermissionTests(AIMarkingTestBase):
    def test_other_teacher_cannot_ai_mark(self):
        self.record_subjective_answer()
        self.auth('ex_teacher_a2')
        response = self.ai_mark()
        self.assertEqual(response.status_code, 404)

    def test_student_cannot_ai_mark(self):
        self.record_subjective_answer()
        self.auth('ex_student_a')
        response = self.ai_mark()
        self.assertEqual(response.status_code, 404)

    def test_admin_can_finalize(self):
        self.mark_everything_manually_via_admin()

    def mark_everything_manually_via_admin(self):
        self.client.post(f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'})
        self.client.post(
            f'/api/exams/answers/{self.subjective_answer_id}/mark/', {'teacher_final_marks': 8},
        )
        script = ExamScript.objects.get(id=self.script_id)
        script.status = ExamScript.Status.MARKED
        script.save(update_fields=['status'])
        self.auth('ex_admin_a')
        self.client.post(f'/api/exams/scripts/{self.script_id}/submit-for-review/')
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/finalize/')
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_cannot_ai_mark(self):
        self.client.credentials()
        response = self.ai_mark()
        self.assertEqual(response.status_code, 401)

class ExamResultsTestBase(ExamMarkingTestBase):
    def setUp(self):
        super().setUp()
        self.marking_mgr_er = create_user(
            User.Role.EXAM_MARKING_MANAGER, school=self.school_a, username='er_marking_mgr',
        )
        self.script_id = ExamScript.objects.get(
            exam_id=self.exam_id, candidate__candidate_number='F2A-001',
        ).id
        self.start_marking()
        self.client.post(f'/api/exams/answers/{self.objective_answer_id}/mark/', {'recorded_answer': 'B'})
        self.client.post(
            f'/api/exams/answers/{self.subjective_answer_id}/mark/',
            {'teacher_final_marks': 8, 'teacher_comment': 'Good explanation, missed one point.'},
        )

    def finalize_script(self):
        self.client.post(f'/api/exams/scripts/{self.script_id}/submit-for-review/')
        self.auth('er_marking_mgr')
        resp = self.client.post(f'/api/exams/scripts/{self.script_id}/finalize/')
        self.auth('ex_teacher_a')
        return resp

class ManualOnlyMarkingWorkflowTests(ExamResultsTestBase):
    """Confirms AI is never a hard requirement to progress a script: when
    every answer already has a teacher_final_marks (set manually, or via
    deterministic objective marking), the script can go straight from
    UPLOADED to TEACHER_REVIEW without ai-mark ever being called."""

    def test_submit_for_review_from_uploaded_when_fully_manually_marked(self):
        script = ExamScript.objects.get(id=self.script_id)
        self.assertEqual(script.status, ExamScript.Status.UPLOADED)

        response = self.client.post(f'/api/exams/scripts/{self.script_id}/submit-for-review/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'TEACHER_REVIEW')

    def test_full_manual_pipeline_to_publish(self):
        """End-to-end: mark everything by hand, submit for review,
        finalize, publish, student sees it -- zero AI calls anywhere."""
        self.finalize_script()
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.assertEqual(response.status_code, 200)

        self.auth('ex_student_a')
        result = self.client.get('/api/exams/results/')
        self.assertEqual(result.data['data']['count'], 1)

    def test_cannot_submit_for_review_from_uploaded_if_any_answer_still_unmarked(self):
        answer = StudentExamAnswer.objects.get(id=self.objective_answer_id)
        answer.teacher_final_marks = None
        answer.save(update_fields=['teacher_final_marks'])

        response = self.client.post(f'/api/exams/scripts/{self.script_id}/submit-for-review/')
        self.assertEqual(response.status_code, 400)

class PublishGateTests(ExamResultsTestBase):
    def test_cannot_publish_unreviewed_result(self):
        """Script is still at UPLOADED (never even started marking) --
        nowhere near FINALIZED."""
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_publish_from_marked_status(self):
        script = ExamScript.objects.get(id=self.script_id)
        script.status = ExamScript.Status.MARKED
        script.save(update_fields=['status'])
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_publish_from_teacher_review_status(self):
        script = ExamScript.objects.get(id=self.script_id)
        script.status = ExamScript.Status.TEACHER_REVIEW
        script.save(update_fields=['status'])
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_publish_failed_ai_marking(self):
        script = ExamScript.objects.get(id=self.script_id)
        script.status = ExamScript.Status.FAILED
        script.marking_error = 'Q2: AI service unavailable.'
        script.save(update_fields=['status', 'marking_error'])
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_finalized_result_can_be_published(self):
        self.finalize_script()
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'PUBLISHED')

    def test_cannot_publish_twice(self):
        self.finalize_script()
        self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_other_teacher_cannot_publish(self):
        self.finalize_script()
        self.auth('ex_teacher_a2')
        response = self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.assertEqual(response.status_code, 404)

class StudentResultVisibilityTests(ExamResultsTestBase):
    def test_student_cannot_see_result_before_published(self):
        self.finalize_script()
        self.auth('ex_student_a')
        response = self.client.get('/api/exams/results/')
        self.assertEqual(response.data['data']['count'], 0)

    def test_student_sees_result_after_published(self):
        self.finalize_script()
        self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')

        self.auth('ex_student_a')
        response = self.client.get('/api/exams/results/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['count'], 1)

        result = response.data['data']['results'][0]
        self.assertEqual(result['candidate_number'], 'F2A-001')
        self.assertEqual(float(result['score']), 13.0)
        self.assertEqual(result['total_marks'], 15)
        self.assertAlmostEqual(float(result['percentage']), 13 / 15 * 100, places=2)

    def test_result_includes_question_level_marks_and_feedback(self):
        self.finalize_script()
        self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.auth('ex_student_a')
        response = self.client.get('/api/exams/results/')
        answers = response.data['data']['results'][0]['answers']
        self.assertEqual(len(answers), 2)

        subjective = next(a for a in answers if a['max_marks'] == 10)
        self.assertEqual(float(subjective['teacher_final_marks']), 8.0)
        self.assertEqual(subjective['teacher_comment'], 'Good explanation, missed one point.')

        objective = next(a for a in answers if a['max_marks'] == 5)
        self.assertEqual(float(objective['teacher_final_marks']), 5.0)

    def test_published_result_never_exposes_ai_fields(self):
        self.finalize_script()
        self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.auth('ex_student_a')
        response = self.client.get('/api/exams/results/')
        answer_fields = set(response.data['data']['results'][0]['answers'][0].keys())
        self.assertNotIn('ai_marks_awarded', answer_fields)
        self.assertNotIn('ai_feedback', answer_fields)
        self.assertNotIn('recorded_answer', answer_fields)

    def test_student_cannot_see_another_students_result(self):
        self.finalize_script()
        self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.auth('ex_student_a2')
        response = self.client.get('/api/exams/results/')
        self.assertEqual(response.data['data']['count'], 0)

    def test_teacher_cannot_use_results_endpoint(self):
        self.finalize_script()
        self.client.post(f'/api/exams/scripts/{self.script_id}/publish/')
        self.auth('ex_teacher_a')
        response = self.client.get('/api/exams/results/')
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_access_results(self):
        self.client.credentials()
        response = self.client.get('/api/exams/results/')
        self.assertEqual(response.status_code, 401)

class BulkPublishResultsTests(ExamResultsTestBase):
    def test_publish_results_publishes_finalized_scripts(self):
        self.finalize_script()
        response = self.client.post(f'/api/exams/{self.exam_id}/publish-results/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['published_count'], 1)

        script = ExamScript.objects.get(id=self.script_id)
        self.assertEqual(script.status, ExamScript.Status.PUBLISHED)

    def test_publish_results_sets_exam_status(self):
        self.finalize_script()
        self.client.post(f'/api/exams/{self.exam_id}/publish-results/')
        exam = Exam.objects.get(id=self.exam_id)
        self.assertEqual(exam.status, Exam.Status.RESULTS_PUBLISHED)

    def test_publish_results_skips_non_finalized_scripts(self):
        """Script is still UPLOADED (never marked) -- publish-results must
        not touch it, and must not error either; it just publishes
        whatever IS ready."""
        response = self.client.post(f'/api/exams/{self.exam_id}/publish-results/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['published_count'], 0)
        script = ExamScript.objects.get(id=self.script_id)
        self.assertEqual(script.status, ExamScript.Status.UPLOADED)

    def test_student_sees_result_after_bulk_publish(self):
        self.finalize_script()
        self.client.post(f'/api/exams/{self.exam_id}/publish-results/')
        self.auth('ex_student_a')
        response = self.client.get('/api/exams/results/')
        self.assertEqual(response.data['data']['count'], 1)

    def test_other_teacher_cannot_bulk_publish(self):
        self.finalize_script()
        self.auth('ex_teacher_a2')
        response = self.client.post(f'/api/exams/{self.exam_id}/publish-results/')
        self.assertEqual(response.status_code, 404)

class Phase2RBACScriptBase(ExamMarkingTestBase):
    """Extends ExamMarkingTestBase with marking-domain users for Phase 2 tests."""

    def setUp(self):
        super().setUp()
        self.marking_mgr = create_user(
            User.Role.EXAM_MARKING_MANAGER, school=self.school_a, username='p2_marking_mgr',
        )
        self.chief_examiner = create_user(
            User.Role.CHIEF_EXAMINER, school=self.school_a, username='p2_chief_examiner',
        )
        self.examiner = create_user(
            User.Role.EXAMINER, school=self.school_a, username='p2_examiner',
        )
        self.examiner_b = create_user(
            User.Role.EXAMINER, school=self.school_b, username='p2_examiner_b',
        )
        self.checker = create_user(
            User.Role.CHECKER, school=self.school_a, username='p2_checker',
        )
        self.script = ExamScript.objects.get(id=ExamScript.objects.filter(
            exam_id=self.exam_id
        ).values_list('id', flat=True).first())

    def assign_examiner(self, examiner=None):
        """Assign examiner to the default script (school-level access, no panel needed)."""
        examiner = examiner or self.examiner
        return ExamScriptAssignment.objects.create(
            script=self.script,
            assignee=examiner,
            role=ExamScriptAssignment.Role.EXAMINER,
            assigned_by=self.admin_a,
        )

    def get_script_detail(self):
        return self.client.get(f'/api/exams/scripts/{self.script.id}/')

    def mark_answer(self, answer_id, **payload):
        return self.client.post(f'/api/exams/answers/{answer_id}/mark/', payload)

    def submit_for_review(self):
        return self.client.post(f'/api/exams/scripts/{self.script.id}/submit-for-review/')

    def finalize_script(self):
        return self.client.post(f'/api/exams/scripts/{self.script.id}/finalize/')

class SystemAdminMarkingExclusionTests(Phase2RBACScriptBase):
    """System admin must be excluded from all routine marking operations."""

    def setUp(self):
        super().setUp()
        self.auth('ex_teacher_a')
        self.start_marking()

    def test_system_admin_cannot_confirm_marks(self):
        self.auth('ex_sysadmin')
        response = self.mark_answer(self.objective_answer_id, recorded_answer='B')
        self.assertEqual(response.status_code, 403)

    def test_system_admin_cannot_submit_for_review(self):
        self.auth('ex_teacher_a')
        self.mark_answer(self.objective_answer_id, recorded_answer='B')
        self.mark_answer(self.subjective_answer_id, teacher_final_marks=8)

        self.auth('ex_sysadmin')
        response = self.submit_for_review()
        self.assertEqual(response.status_code, 403)

    def test_system_admin_cannot_finalize_script(self):
        self.auth('ex_teacher_a')
        self.mark_answer(self.objective_answer_id, recorded_answer='B')
        self.mark_answer(self.subjective_answer_id, teacher_final_marks=8)
        self.submit_for_review()

        self.auth('ex_sysadmin')
        response = self.finalize_script()
        self.assertEqual(response.status_code, 403)

class MarkingManagerScriptAccessTests(Phase2RBACScriptBase):
    """EXAM_MARKING_MANAGER can manage scripts for their own school."""

    def setUp(self):
        super().setUp()
        self.auth('ex_teacher_a')
        self.start_marking()
        self.mark_answer(self.objective_answer_id, recorded_answer='B')
        self.mark_answer(self.subjective_answer_id, teacher_final_marks=8)
        self.submit_for_review()

    def test_marking_manager_can_see_school_scripts(self):
        self.auth('p2_marking_mgr')
        response = self.client.get(f'/api/exams/{self.exam_id}/scripts/')
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data['data']), 0)

    def test_marking_manager_can_finalize_script(self):
        self.auth('p2_marking_mgr')
        response = self.finalize_script()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'FINALIZED')

    def test_marking_manager_other_school_cannot_see_scripts(self):
        create_user(
            User.Role.EXAM_MARKING_MANAGER, school=self.school_b, username='p2_marking_mgr_b',
        )
        self.auth('p2_marking_mgr_b')
        response = self.client.get(f'/api/exams/{self.exam_id}/scripts/')
        self.assertEqual(response.status_code, 404)

class ExaminerAssignmentScopingTests(Phase2RBACScriptBase):
    """EXAMINER visibility and marking must be scoped to their assignments."""

    def setUp(self):
        super().setUp()
        self.auth('ex_teacher_a')
        self.start_marking()

    def test_examiner_at_school_can_see_marking_exam(self):
        self.auth('p2_examiner')
        response = self.client.get(f'/api/exams/{self.exam_id}/scripts/')
        self.assertEqual(response.status_code, 200)

    def test_examiner_with_assignment_can_see_script(self):
        self.assign_examiner()
        self.auth('p2_examiner')
        response = self.client.get(f'/api/exams/{self.exam_id}/scripts/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['data']), 1)

    def test_examiner_at_school_can_mark_without_prior_assignment(self):
        self.auth('p2_examiner')
        response = self.mark_answer(self.objective_answer_id, recorded_answer='B')
        self.assertEqual(response.status_code, 200)

    def test_examiner_with_assignment_can_mark(self):
        self.assign_examiner()
        self.auth('p2_examiner')
        response = self.mark_answer(self.objective_answer_id, recorded_answer='B')
        self.assertEqual(response.status_code, 200)

    def test_examiner_different_school_cannot_see_scripts(self):
        self.assign_examiner(examiner=self.examiner_b)
        self.auth('p2_examiner_b')
        response = self.client.get(f'/api/exams/{self.exam_id}/scripts/')
        self.assertEqual(response.status_code, 404)

class Phase7Base(Phase2RBACScriptBase):
    """Common setUp: marking started, all answers marked, ready for review."""

    def setUp(self):
        super().setUp()
        self.data_entry = create_user(
            User.Role.DATA_ENTRY_CLERK, school=self.school_a, username='p7_data_entry',
        )
        self.it_support = create_user(
            User.Role.IT_SUPPORT, school=self.school_a, username='p7_it_support',
        )
        self.auth('ex_teacher_a')
        self.start_marking()

    def _mark_all_answers(self):
        """Mark both answers so submit_for_review can succeed."""
        self.auth('ex_teacher_a')
        self.mark_answer(self.objective_answer_id, recorded_answer='B')
        self.mark_answer(self.subjective_answer_id, teacher_final_marks=8)

    def _advance_to_teacher_review(self):
        self._mark_all_answers()
        self.submit_for_review()

    def _advance_to_finalized(self):
        self._advance_to_teacher_review()
        self.auth('p2_marking_mgr')
        self.finalize_script()

class CheckerDataEntryAccessTests(Phase7Base):
    """MVP: CHECKER, DATA_ENTRY_CLERK, and IT_SUPPORT are removed from
    active marking roles. Verify they can no longer access marking resources."""

    def test_checker_cannot_see_scripts(self):
        self.auth('p2_checker')
        response = self.client.get(f'/api/exams/{self.exam_id}/scripts/')
        self.assertEqual(response.status_code, 404)

    def test_checker_cannot_finalize_script(self):
        self._advance_to_teacher_review()
        self.auth('p2_checker')
        response = self.finalize_script()
        self.assertEqual(response.status_code, 404)

    def test_data_entry_clerk_cannot_see_scripts(self):
        self.auth('p7_data_entry')
        response = self.client.get(f'/api/exams/{self.exam_id}/scripts/')
        self.assertEqual(response.status_code, 404)

    def test_data_entry_clerk_cannot_mark_answers(self):
        self.auth('p7_data_entry')
        response = self.mark_answer(self.subjective_answer_id, teacher_final_marks=7)
        self.assertEqual(response.status_code, 404)

    def test_it_support_cannot_see_scripts(self):
        self.auth('p7_it_support')
        response = self.client.get(f'/api/exams/{self.exam_id}/scripts/')
        self.assertEqual(response.status_code, 404)

    def test_it_support_cannot_mark_answers(self):
        self.auth('p7_it_support')
        response = self.mark_answer(self.subjective_answer_id, teacher_final_marks=7)
        self.assertEqual(response.status_code, 404)

    def test_it_support_cannot_finalize_script(self):
        self._advance_to_teacher_review()
        self.auth('p7_it_support')
        response = self.finalize_script()
        self.assertEqual(response.status_code, 404)

    def test_it_support_cannot_submit_for_review(self):
        self._mark_all_answers()
        self.auth('p7_it_support')
        response = self.submit_for_review()
        self.assertEqual(response.status_code, 404)

class AuditLogCreationTests(Phase7Base):
    """Verify that state-changing actions create MarkingAuditLog entries."""

    def test_submit_for_review_creates_audit_entry(self):
        self._mark_all_answers()
        initial_count = MarkingAuditLog.objects.filter(
            action=MarkingAuditLog.Action.SCRIPT_SUBMITTED,
        ).count()
        self.auth('ex_teacher_a')
        self.submit_for_review()
        self.assertEqual(
            MarkingAuditLog.objects.filter(action=MarkingAuditLog.Action.SCRIPT_SUBMITTED).count(),
            initial_count + 1,
        )

    def test_finalize_creates_audit_entry(self):
        self._advance_to_teacher_review()
        initial_count = MarkingAuditLog.objects.filter(
            action=MarkingAuditLog.Action.SCRIPT_FINALIZED,
        ).count()
        self.auth('p2_marking_mgr')
        self.finalize_script()
        entry = MarkingAuditLog.objects.filter(
            action=MarkingAuditLog.Action.SCRIPT_FINALIZED,
        ).order_by('-created_at').first()
        self.assertEqual(
            MarkingAuditLog.objects.filter(action=MarkingAuditLog.Action.SCRIPT_FINALIZED).count(),
            initial_count + 1,
        )
        self.assertIn('score', entry.detail)

    def test_answer_mark_creates_audit_entry(self):
        initial_count = MarkingAuditLog.objects.filter(
            action=MarkingAuditLog.Action.ANSWER_MARKED,
        ).count()
        self.auth('ex_teacher_a')
        self.mark_answer(self.subjective_answer_id, teacher_final_marks=9)
        self.assertEqual(
            MarkingAuditLog.objects.filter(action=MarkingAuditLog.Action.ANSWER_MARKED).count(),
            initial_count + 1,
        )

    def test_audit_entry_snapshots_actor_username(self):
        self.auth('p2_marking_mgr')
        self._advance_to_teacher_review()
        self.auth('p2_marking_mgr')
        self.finalize_script()
        entry = MarkingAuditLog.objects.filter(
            action=MarkingAuditLog.Action.SCRIPT_FINALIZED,
            actor=self.marking_mgr,
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.actor_username, 'p2_marking_mgr')
        self.assertEqual(entry.actor_role, 'EXAM_MARKING_MANAGER')

    def test_audit_entry_linked_to_correct_exam(self):
        self._mark_all_answers()
        self.auth('ex_teacher_a')
        self.submit_for_review()
        entry = MarkingAuditLog.objects.filter(
            action=MarkingAuditLog.Action.SCRIPT_SUBMITTED,
        ).order_by('-created_at').first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.exam_id, self.exam_id)

class AuditLogAPITests(Phase7Base):
    """EXAM_MARKING_MANAGER can query the audit log; other roles cannot."""

    def setUp(self):
        super().setUp()
        self._advance_to_teacher_review()

    def test_marking_manager_can_list_audit_log(self):
        self.auth('p2_marking_mgr')
        response = self.client.get('/api/exams/audit/')
        self.assertEqual(response.status_code, 200)

    def test_audit_log_contains_submit_entry(self):
        self.auth('p2_marking_mgr')
        response = self.client.get(f'/api/exams/audit/?exam={self.exam_id}')
        self.assertEqual(response.status_code, 200)
        actions = [e['action'] for e in response.data['data']]
        self.assertIn('SCRIPT_SUBMITTED', actions)

    def test_chief_examiner_cannot_access_audit_log(self):
        self.auth('p2_chief_examiner')
        response = self.client.get('/api/exams/audit/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['data']), 0)

    def test_examiner_cannot_access_audit_log(self):
        self.auth('p2_examiner')
        response = self.client.get('/api/exams/audit/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['data']), 0)

    def test_cross_school_audit_isolation(self):
        mgr_b = create_user(
            User.Role.EXAM_MARKING_MANAGER, school=self.school_b, username='p7_mgr_b',
        )
        self.auth('p7_mgr_b')
        response = self.client.get(f'/api/exams/audit/?exam={self.exam_id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['data']), 0)

    def test_audit_log_action_filter(self):
        self.auth('p2_marking_mgr')
        response = self.client.get(f'/api/exams/audit/?action=SCRIPT_SUBMITTED')
        self.assertEqual(response.status_code, 200)
        for entry in response.data['data']:
            self.assertEqual(entry['action'], 'SCRIPT_SUBMITTED')

    def test_unauthenticated_cannot_access_audit_log(self):
        self.client.credentials()
        response = self.client.get('/api/exams/audit/')
        self.assertEqual(response.status_code, 401)

class ScriptAssignmentAPITests(Phase7Base):
    """POST/DELETE /api/exams/scripts/:id/assign/ — assignment management."""

    def test_marking_manager_can_assign_examiner(self):
        self.auth('p2_marking_mgr')
        response = self.client.post(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.examiner.id, 'role': 'EXAMINER'},
        )
        self.assertIn(response.status_code, (200, 201))
        self.assertEqual(response.data['data']['assignee_username'], 'p2_examiner')
        self.assertEqual(response.data['data']['role'], 'EXAMINER')

    def test_chief_examiner_cannot_assign(self):
        self.auth('p2_chief_examiner')
        response = self.client.post(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.examiner.id, 'role': 'EXAMINER'},
        )
        self.assertEqual(response.status_code, 404)

    def test_examiner_cannot_assign(self):
        self.auth('p2_examiner')
        response = self.client.post(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.checker.id, 'role': 'EXAMINER'},
        )
        self.assertEqual(response.status_code, 403)

    def test_assigning_non_school_user_fails(self):
        self.auth('p2_marking_mgr')
        response = self.client.post(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.examiner_b.id, 'role': 'EXAMINER'},
        )
        self.assertEqual(response.status_code, 400)

    def test_assigning_teacher_without_secondary_role_fails(self):
        self.auth('p2_marking_mgr')
        response = self.client.post(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.teacher_a.id, 'role': 'EXAMINER'},
        )
        self.assertEqual(response.status_code, 400)

    def test_assigning_teacher_with_examiner_secondary_role_succeeds(self):
        self.teacher_a.secondary_role = 'EXAMINER'
        self.teacher_a.save(update_fields=['secondary_role'])
        self.auth('p2_marking_mgr')
        response = self.client.post(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.teacher_a.id, 'role': 'EXAMINER'},
        )
        self.assertIn(response.status_code, (200, 201))
        self.assertEqual(response.data['data']['assignee_username'], 'ex_teacher_a')

    def test_get_assignments_lists_current_assignments(self):
        self.assign_examiner()
        self.auth('p2_marking_mgr')
        response = self.client.get(f'/api/exams/scripts/{self.script.id}/assign/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['data']), 1)
        self.assertEqual(response.data['data'][0]['role'], 'EXAMINER')

    def test_delete_removes_assignment(self):
        self.assign_examiner()
        self.auth('p2_marking_mgr')
        response = self.client.delete(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.examiner.id, 'role': 'EXAMINER'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            ExamScriptAssignment.objects.filter(script=self.script, assignee=self.examiner).exists(),
        )

    def test_assignment_creates_audit_entry(self):
        self.auth('p2_marking_mgr')
        self.client.post(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.examiner.id, 'role': 'EXAMINER'},
        )
        self.assertTrue(
            MarkingAuditLog.objects.filter(
                action=MarkingAuditLog.Action.SCRIPT_ASSIGNED,
                script=self.script,
            ).exists(),
        )

    def test_unassign_creates_audit_entry(self):
        self.assign_examiner()
        self.auth('p2_marking_mgr')
        self.client.delete(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.examiner.id, 'role': 'EXAMINER'},
            format='json',
        )
        self.assertTrue(
            MarkingAuditLog.objects.filter(
                action=MarkingAuditLog.Action.SCRIPT_UNASSIGNED,
                script=self.script,
            ).exists(),
        )

class EmergencyResetTests(Phase7Base):
    """POST /api/exams/scripts/:id/reset/ — emergency script reset."""

    def _force_script_status(self, new_status):
        self.script.status = new_status
        self.script.marking_error = 'Simulated failure'
        self.script.save(update_fields=['status', 'marking_error', 'updated_at'])

    def test_manager_can_reset_failed_script(self):
        self._force_script_status(ExamScript.Status.FAILED)
        self.auth('p2_marking_mgr')
        response = self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'UPLOADED')

    def test_reset_clears_marking_error(self):
        self._force_script_status(ExamScript.Status.FAILED)
        self.auth('p2_marking_mgr')
        self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.script.refresh_from_db()
        self.assertEqual(self.script.marking_error, '')

    def test_manager_can_reset_processing_script(self):
        self._force_script_status(ExamScript.Status.PROCESSING)
        self.auth('p2_marking_mgr')
        response = self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'UPLOADED')

    def test_chief_examiner_cannot_reset(self):
        self._force_script_status(ExamScript.Status.FAILED)
        self.auth('p2_chief_examiner')
        response = self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.assertEqual(response.status_code, 404)

    def test_examiner_cannot_reset(self):
        self._force_script_status(ExamScript.Status.FAILED)
        self.auth('p2_examiner')
        response = self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.assertEqual(response.status_code, 403)

    def test_cannot_reset_uploaded_script(self):
        self.auth('p2_marking_mgr')
        response = self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.assertEqual(response.status_code, 400)

    def test_cannot_reset_finalized_script(self):
        self._advance_to_finalized()
        self.auth('p2_marking_mgr')
        response = self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.assertEqual(response.status_code, 400)

    def test_reset_creates_audit_entry(self):
        self._force_script_status(ExamScript.Status.FAILED)
        self.auth('p2_marking_mgr')
        self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.assertTrue(
            MarkingAuditLog.objects.filter(
                action=MarkingAuditLog.Action.SCRIPT_RESET,
                script=self.script,
            ).exists(),
        )

    def test_reset_audit_detail_records_from_status(self):
        self._force_script_status(ExamScript.Status.FAILED)
        self.auth('p2_marking_mgr')
        self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        entry = MarkingAuditLog.objects.filter(
            action=MarkingAuditLog.Action.SCRIPT_RESET,
        ).order_by('-created_at').first()
        self.assertEqual(entry.detail.get('from_status'), 'FAILED')

    def test_cross_school_manager_cannot_reset(self):
        self._force_script_status(ExamScript.Status.FAILED)
        mgr_b = create_user(
            User.Role.EXAM_MARKING_MANAGER, school=self.school_b, username='p7_mgr_b_reset',
        )
        self.auth('p7_mgr_b_reset')
        response = self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.assertEqual(response.status_code, 404)

class FullMarkingWorkflowIntegrationTests(Phase7Base):
    """End-to-end integration: covers the complete marking lifecycle with
    role-appropriate actors at each step, and verifies audit trail correctness."""

    def test_complete_examiner_led_workflow(self):
        """EXAM_MARKING_MANAGER assigns → EXAMINER marks and submits → MANAGER finalizes."""
        self.auth('p2_marking_mgr')
        assign_resp = self.client.post(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.examiner.id, 'role': 'EXAMINER'},
        )
        self.assertIn(assign_resp.status_code, (200, 201))

        self.auth('p2_examiner')
        mark1 = self.mark_answer(self.objective_answer_id, recorded_answer='B')
        mark2 = self.mark_answer(self.subjective_answer_id, teacher_final_marks=7)
        self.assertEqual(mark1.status_code, 200)
        self.assertEqual(mark2.status_code, 200)

        submit_resp = self.submit_for_review()
        self.assertEqual(submit_resp.status_code, 200)
        self.assertEqual(submit_resp.data['data']['status'], 'TEACHER_REVIEW')

        self.auth('p2_marking_mgr')
        finalize_resp = self.finalize_script()
        self.assertEqual(finalize_resp.status_code, 200)
        self.assertEqual(finalize_resp.data['data']['status'], 'FINALIZED')

    def test_full_workflow_audit_trail_completeness(self):
        """After a complete workflow, the audit log must contain exactly the
        expected sequence of action types."""
        self.auth('p2_marking_mgr')
        self.client.post(
            f'/api/exams/scripts/{self.script.id}/assign/',
            {'assignee_id': self.examiner.id, 'role': 'EXAMINER'},
        )

        self.auth('p2_examiner')
        self.mark_answer(self.objective_answer_id, recorded_answer='B')
        self.mark_answer(self.subjective_answer_id, teacher_final_marks=8)
        self.submit_for_review()

        self.auth('p2_marking_mgr')
        self.finalize_script()

        log_actions = list(
            MarkingAuditLog.objects.filter(script=self.script)
            .order_by('created_at')
            .values_list('action', flat=True),
        )
        self.assertIn(MarkingAuditLog.Action.SCRIPT_ASSIGNED, log_actions)
        self.assertIn(MarkingAuditLog.Action.ANSWER_MARKED, log_actions)
        self.assertIn(MarkingAuditLog.Action.SCRIPT_SUBMITTED, log_actions)
        self.assertIn(MarkingAuditLog.Action.SCRIPT_FINALIZED, log_actions)

    def test_failed_then_reset_then_full_workflow(self):
        """FAILED script can be reset, re-marked, and completed normally."""
        self.script.status = ExamScript.Status.FAILED
        self.script.marking_error = 'AI timeout'
        self.script.save(update_fields=['status', 'marking_error', 'updated_at'])

        self.auth('p2_marking_mgr')
        reset_resp = self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.assertEqual(reset_resp.status_code, 200)
        self.assertEqual(reset_resp.data['data']['status'], 'UPLOADED')

        self.auth('ex_teacher_a')
        self.mark_answer(self.objective_answer_id, recorded_answer='B')
        self.mark_answer(self.subjective_answer_id, teacher_final_marks=6)
        submit_resp = self.submit_for_review()
        self.assertEqual(submit_resp.status_code, 200)

        self.auth('p2_marking_mgr')
        finalize_resp = self.finalize_script()
        self.assertEqual(finalize_resp.status_code, 200)
        self.assertEqual(finalize_resp.data['data']['status'], 'FINALIZED')

    def test_scripts_endpoint_enriched_with_candidate_number(self):
        """GET /api/exams/scripts/:id/answers/ returns question detail fields."""
        self.assign_examiner()
        self.auth('p2_examiner')
        response = self.client.get(f'/api/exams/scripts/{self.script.id}/answers/')
        self.assertEqual(response.status_code, 200)
        answers = response.data['data']
        self.assertTrue(len(answers) > 0)
        first = answers[0]
        self.assertIn('question_number', first)
        self.assertIn('question_text', first)
        self.assertIn('max_marks', first)
        self.assertIn('question_type', first)

    def test_script_status_filter_on_list_endpoint(self):
        """GET /api/exams/scripts/?status=UPLOADED returns only matching scripts."""
        self.auth('p2_marking_mgr')
        response = self.client.get('/api/exams/scripts/?status=UPLOADED')
        self.assertEqual(response.status_code, 200)
        for script in response.data['data']:
            self.assertEqual(script['status'], 'UPLOADED')

    def test_system_admin_cannot_reset_script(self):
        """System admin is explicitly excluded from all routine marking ops."""
        self.script.status = ExamScript.Status.FAILED
        self.script.save(update_fields=['status', 'updated_at'])
        self.auth('ex_sysadmin')
        response = self.client.post(f'/api/exams/scripts/{self.script.id}/reset/')
        self.assertEqual(response.status_code, 403)

