from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from apps.academics.models import ClassLevel, LearningObjective, Subject, Topic
from apps.accounts.models import User
from apps.classes.models import SchoolClass
from apps.quizzes.models import Quiz
from apps.schools.models import School

from .models import (
    Lesson, LessonAssignment, LessonAttachment, LessonSection, SectionContentBlock, SectionMiniQuiz,
    StudentSectionProgress,
)

def create_user(role, school=None, admin_scope=None, username=None, password='pass12345'):
    username = username or f'{role.lower()}-{school.code if school else "sys"}-{admin_scope or ""}'
    user = User(role=role, admin_scope=admin_scope, school=school, username=username)
    user.set_password(password)
    user.save()
    return user

class LessonsTestBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school_a = School.objects.create(name='School A', code='LSN-A')
        self.school_b = School.objects.create(name='School B', code='LSN-B')

        self.sys_admin = create_user(User.Role.ADMIN, admin_scope=User.AdminScope.SYSTEM, username='ls_sysadmin')
        self.admin_a = create_user(
            User.Role.ADMIN, school=self.school_a, admin_scope=User.AdminScope.SCHOOL, username='ls_admin_a',
        )
        self.teacher_a = create_user(User.Role.TEACHER, school=self.school_a, username='ls_teacher_a')
        self.teacher_a2 = create_user(User.Role.TEACHER, school=self.school_a, username='ls_teacher_a2')
        self.teacher_b = create_user(User.Role.TEACHER, school=self.school_b, username='ls_teacher_b')
        self.student_a = create_user(User.Role.STUDENT, school=self.school_a, username='ls_student_a')

        self.form_2 = ClassLevel.objects.get(code='F2')
        self.subject_a = Subject.objects.create(school=self.school_a, name='Mathematics')
        self.subject_b = Subject.objects.create(school=self.school_b, name='Mathematics')
        self.topic_a = Topic.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2, title='Algebra',
        )
        self.objective_a = LearningObjective.objects.create(
            school=self.school_a, topic=self.topic_a, description='Solve linear equations',
        )
        self.class_a = SchoolClass.objects.create(
            school=self.school_a, name='Form II Blue', class_level=self.form_2, academic_year=2026,
        )

    def auth(self, username, password='pass12345'):
        login = self.client.post('/api/auth/login/', {'username': username, 'password': password})
        access = login.data['data']['access']
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

    def create_lesson(self, **overrides):
        payload = {
            'subject': self.subject_a.id, 'class_level': self.form_2.id, 'title': 'Intro to Algebra',
        }
        payload.update(overrides)
        return self.client.post('/api/lessons/', payload)

class TeacherPermissionTests(LessonsTestBase):
    def test_teacher_can_create_lesson(self):
        self.auth('ls_teacher_a')
        response = self.create_lesson()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['data']['status'], 'DRAFT')
        self.assertEqual(response.data['data']['created_by'], self.teacher_a.id)

    def test_created_by_is_not_client_settable(self):
        self.auth('ls_teacher_a')
        response = self.create_lesson(created_by=self.teacher_a2.id)
        self.assertEqual(response.data['data']['created_by'], self.teacher_a.id)

    def test_student_cannot_create_lesson(self):
        self.auth('ls_student_a')
        response = self.create_lesson()
        self.assertEqual(response.status_code, 403)

    def test_teacher_can_update_own_lesson(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        response = self.client.patch(f'/api/lessons/{lesson_id}/', {'title': 'Updated title'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['title'], 'Updated title')

    def test_teacher_cannot_update_another_teachers_draft_lesson(self):
        """Not visible to teacher_a2 at all (draft, not theirs) -> 404, not
        403 -- consistent with the rest of the app never confirming the
        existence of something outside the caller's visibility."""
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.auth('ls_teacher_a2')
        response = self.client.patch(f'/api/lessons/{lesson_id}/', {'title': 'Hijacked'})
        self.assertEqual(response.status_code, 404)

    def test_teacher_cannot_update_another_teachers_published_lesson(self):
        """Once published it IS visible to teacher_a2, so this exercises
        the real ownership check (IsOwnerOrSchoolAdmin) -> 403."""
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        self.auth('ls_teacher_a2')
        response = self.client.patch(f'/api/lessons/{lesson_id}/', {'title': 'Hijacked'})
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_see_another_teachers_draft(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.auth('ls_teacher_a2')
        response = self.client.get(f'/api/lessons/{lesson_id}/')
        self.assertEqual(response.status_code, 404)

    def test_student_cannot_see_draft_lesson(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/{lesson_id}/')
        self.assertEqual(response.status_code, 404)

    def test_teacher_can_delete_own_draft_lesson(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        response = self.client.delete(f'/api/lessons/{lesson_id}/')
        self.assertEqual(response.status_code, 204)

    def test_cannot_delete_published_lesson(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        response = self.client.delete(f'/api/lessons/{lesson_id}/')
        self.assertEqual(response.status_code, 400)

    def test_lesson_topic_must_match_subject(self):
        other_subject = Subject.objects.create(school=self.school_a, name='Biology')
        self.auth('ls_teacher_a')
        response = self.create_lesson(subject=other_subject.id, topic=self.topic_a.id)
        self.assertEqual(response.status_code, 400)

    def test_lesson_learning_objective_requires_matching_topic(self):
        other_topic = Topic.objects.create(
            school=self.school_a, subject=self.subject_a, class_level=self.form_2, title='Geometry',
        )
        self.auth('ls_teacher_a')
        response = self.create_lesson(topic=other_topic.id, learning_objective=self.objective_a.id)
        self.assertEqual(response.status_code, 400)

class SchoolAdminPermissionTests(LessonsTestBase):
    def test_admin_can_see_all_lessons_in_school_regardless_of_status(self):
        self.auth('ls_teacher_a')
        self.create_lesson()
        self.auth('ls_admin_a')
        response = self.client.get('/api/lessons/')
        self.assertEqual(response.data['data']['count'], 1)

    def test_admin_can_edit_any_teachers_lesson(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.auth('ls_admin_a')
        response = self.client.patch(f'/api/lessons/{lesson_id}/', {'title': 'Admin edited'})
        self.assertEqual(response.status_code, 200)

    def test_admin_can_publish_a_teachers_lesson(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.auth('ls_admin_a')
        response = self.client.post(f'/api/lessons/{lesson_id}/publish/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'PUBLISHED')

    def test_admin_cannot_see_or_edit_other_school_lesson(self):
        self.auth('ls_teacher_b')
        lesson_id = self.create_lesson(subject=self.subject_b.id).data['data']['id']
        self.auth('ls_admin_a')
        response = self.client.get(f'/api/lessons/{lesson_id}/')
        self.assertEqual(response.status_code, 404)

    def test_admin_create_without_school_fails(self):
        self.auth('ls_sysadmin')
        response = self.client.post('/api/lessons/', {
            'subject': self.subject_a.id, 'class_level': self.form_2.id, 'title': 'X',
        })
        self.assertEqual(response.status_code, 400)

class PublicationWorkflowTests(LessonsTestBase):
    def test_publish_sets_status_and_published_at(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        response = self.client.post(f'/api/lessons/{lesson_id}/publish/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'PUBLISHED')
        self.assertIsNotNone(response.data['data']['published_at'])

    def test_cannot_publish_already_published_lesson(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        response = self.client.post(f'/api/lessons/{lesson_id}/publish/')
        self.assertEqual(response.status_code, 400)

    def test_archive_sets_status(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        response = self.client.post(f'/api/lessons/{lesson_id}/archive/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['data']['status'], 'ARCHIVED')

    def test_status_field_ignored_on_plain_patch(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        response = self.client.patch(f'/api/lessons/{lesson_id}/', {'status': 'PUBLISHED'})
        self.assertEqual(response.data['data']['status'], 'DRAFT')

    def test_student_does_not_see_archived_lesson(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        self.client.post(f'/api/lessons/{lesson_id}/archive/')
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/{lesson_id}/')
        self.assertEqual(response.status_code, 404)

    def test_student_sees_published_lesson(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/{lesson_id}/')
        self.assertEqual(response.status_code, 200)

    def test_cannot_assign_unpublished_lesson_to_class(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        response = self.client.post(f'/api/lessons/{lesson_id}/assign/', {'school_class': self.class_a.id})
        self.assertEqual(response.status_code, 400)

    def test_can_assign_published_lesson_to_own_school_class(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        response = self.client.post(f'/api/lessons/{lesson_id}/assign/', {'school_class': self.class_a.id})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(LessonAssignment.objects.filter(lesson_id=lesson_id, school_class=self.class_a).exists())

    def test_cannot_assign_lesson_to_other_school_class(self):
        other_class = SchoolClass.objects.create(
            school=self.school_b, name='F2B', class_level=self.form_2, academic_year=2026,
        )
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        response = self.client.post(f'/api/lessons/{lesson_id}/assign/', {'school_class': other_class.id})
        self.assertEqual(response.status_code, 400)

    def test_other_teacher_cannot_assign_lesson_they_do_not_own(self):
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        self.auth('ls_teacher_a2')
        response = self.client.post(f'/api/lessons/{lesson_id}/assign/', {'school_class': self.class_a.id})
        self.assertEqual(response.status_code, 403)

class AttachmentValidationTests(LessonsTestBase):
    def setUp(self):
        super().setUp()
        self.auth('ls_teacher_a')
        self.lesson_id = self.create_lesson().data['data']['id']

    def test_teacher_can_upload_valid_attachment(self):
        file = SimpleUploadedFile('notes.pdf', b'%PDF-1.4 fake pdf content', content_type='application/pdf')
        response = self.client.post(f'/api/lessons/{self.lesson_id}/attachments/', {'file': file}, format='multipart')
        self.assertEqual(response.status_code, 201)
        data = response.data['data']
        self.assertEqual(data['file_name'], 'notes.pdf')
        self.assertEqual(data['mime_type'], 'application/pdf')
        self.assertEqual(data['file_size'], len(b'%PDF-1.4 fake pdf content'))
        self.assertTrue(data['checksum'])
        self.assertEqual(data['version'], 1)

    def test_disallowed_file_extension_rejected(self):
        file = SimpleUploadedFile('virus.exe', b'MZ fake exe', content_type='application/octet-stream')
        response = self.client.post(f'/api/lessons/{self.lesson_id}/attachments/', {'file': file}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_oversized_file_rejected(self):
        big_content = b'a' * (11 * 1024 * 1024)
        file = SimpleUploadedFile('big.pdf', big_content, content_type='application/pdf')
        response = self.client.post(f'/api/lessons/{self.lesson_id}/attachments/', {'file': file}, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_reuploading_same_filename_increments_version(self):
        file1 = SimpleUploadedFile('notes.pdf', b'version one', content_type='application/pdf')
        self.client.post(f'/api/lessons/{self.lesson_id}/attachments/', {'file': file1}, format='multipart')
        file2 = SimpleUploadedFile('notes.pdf', b'version two content', content_type='application/pdf')
        response = self.client.post(f'/api/lessons/{self.lesson_id}/attachments/', {'file': file2}, format='multipart')
        self.assertEqual(response.data['data']['version'], 2)

    def test_checksum_is_not_client_settable(self):
        file = SimpleUploadedFile('notes.pdf', b'real content', content_type='application/pdf')
        response = self.client.post(
            f'/api/lessons/{self.lesson_id}/attachments/',
            {'file': file, 'checksum': 'totally-fake-checksum'}, format='multipart',
        )
        self.assertNotEqual(response.data['data']['checksum'], 'totally-fake-checksum')

    def test_other_teacher_cannot_upload_attachment_to_published_lesson(self):
        self.client.post(f'/api/lessons/{self.lesson_id}/publish/')
        self.auth('ls_teacher_a2')
        file = SimpleUploadedFile('notes.pdf', b'sneaky', content_type='application/pdf')
        response = self.client.post(f'/api/lessons/{self.lesson_id}/attachments/', {'file': file}, format='multipart')
        self.assertEqual(response.status_code, 403)

    def test_student_can_view_attachments_of_published_lesson(self):
        file = SimpleUploadedFile('notes.pdf', b'content', content_type='application/pdf')
        self.client.post(f'/api/lessons/{self.lesson_id}/attachments/', {'file': file}, format='multipart')
        self.client.post(f'/api/lessons/{self.lesson_id}/publish/')
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/{self.lesson_id}/attachments/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['data']), 1)

    def test_owner_can_delete_attachment(self):
        file = SimpleUploadedFile('notes.pdf', b'content', content_type='application/pdf')
        upload = self.client.post(f'/api/lessons/{self.lesson_id}/attachments/', {'file': file}, format='multipart')
        attachment_id = upload.data['data']['id']
        response = self.client.delete(
            f'/api/lessons/{self.lesson_id}/attachments/', {'attachment_id': attachment_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(LessonAttachment.objects.filter(id=attachment_id).exists())

class CrossSchoolIsolationTests(LessonsTestBase):
    def test_teacher_cannot_create_lesson_with_other_school_subject(self):
        self.auth('ls_teacher_a')
        response = self.create_lesson(subject=self.subject_b.id)
        self.assertEqual(response.status_code, 400)

    def test_teacher_cannot_list_other_school_lessons(self):
        self.auth('ls_teacher_b')
        lesson_id = self.create_lesson(subject=self.subject_b.id).data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')

        self.auth('ls_teacher_a')
        response = self.client.get('/api/lessons/')
        self.assertEqual(response.data['data']['count'], 0)

    def test_admin_cannot_assign_lesson_across_schools(self):
        other_class = SchoolClass.objects.create(
            school=self.school_b, name='F2B', class_level=self.form_2, academic_year=2026,
        )
        self.auth('ls_teacher_a')
        lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{lesson_id}/publish/')
        response = self.client.post(f'/api/lessons/{lesson_id}/assign/', {'school_class': other_class.id})
        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_cannot_access_lessons(self):
        response = self.client.get('/api/lessons/')
        self.assertEqual(response.status_code, 401)

class SectionsTestBase(LessonsTestBase):
    """A published lesson with two sections: Section 1 has a content block
    and a published one-question mini-quiz (pass >= 70%); Section 2 has
    only a content block, no mini-quiz -- exercising both "gated by a
    quiz" and "not gated at all" section shapes."""

    def setUp(self):
        super().setUp()
        self.student_b = create_user(User.Role.STUDENT, school=self.school_b, username='ls_student_b')

        self.auth('ls_teacher_a')
        self.lesson_id = self.create_lesson().data['data']['id']
        self.client.post(f'/api/lessons/{self.lesson_id}/publish/')

        self.section1_id = self.client.post('/api/lessons/sections/', {
            'lesson': self.lesson_id, 'title': 'Section 1', 'order': 1,
        }).data['data']['id']
        self.section2_id = self.client.post('/api/lessons/sections/', {
            'lesson': self.lesson_id, 'title': 'Section 2', 'order': 2,
        }).data['data']['id']

        self.client.post(f'/api/lessons/sections/{self.section1_id}/content-blocks/', {
            'block_type': 'TEXT', 'content': 'Welcome to section 1.', 'order': 1,
        })
        self.client.post(f'/api/lessons/sections/{self.section2_id}/content-blocks/', {
            'block_type': 'TEXT', 'content': 'Welcome to section 2.', 'order': 1,
        })

        mini_quiz_resp = self.client.post(f'/api/lessons/sections/{self.section1_id}/mini-quiz/', {
            'passing_score': 70,
        })
        self.mini_quiz_quiz_id = mini_quiz_resp.data['data']['quiz']

        self.question_id = self.client.post('/api/quizzes/questions/', {
            'quiz': self.mini_quiz_quiz_id, 'question_type': 'MCQ', 'text': '2 + 2 = ?',
            'marks': 1, 'order': 1, 'explanation': 'Basic addition: 2 + 2 = 4.',
        }).data['data']['id']
        self.wrong_option_id = self.client.post(f'/api/quizzes/questions/{self.question_id}/options/', {
            'text': '5', 'is_correct': False, 'order': 1,
        }).data['data']['id']
        self.correct_option_id = self.client.post(f'/api/quizzes/questions/{self.question_id}/options/', {
            'text': '4', 'is_correct': True, 'order': 2,
        }).data['data']['id']
        self.client.post(f'/api/quizzes/{self.mini_quiz_quiz_id}/publish/')

    def submit(self, section_id, option_id, username='ls_student_a'):
        self.auth(username)
        return self.client.post(f'/api/lessons/sections/{section_id}/submit-quiz/', {
            'answers': [{'question': self.question_id, 'selected_option': option_id}],
        }, format='json')

class SectionAuthoringTests(SectionsTestBase):
    def test_teacher_can_list_sections_for_their_lesson(self):
        self.auth('ls_teacher_a')
        response = self.client.get(f'/api/lessons/sections/?lesson={self.lesson_id}')
        self.assertEqual(response.data['data']['count'], 2)

    def test_duplicate_order_in_same_lesson_rejected(self):
        self.auth('ls_teacher_a')
        response = self.client.post('/api/lessons/sections/', {
            'lesson': self.lesson_id, 'title': 'Duplicate order', 'order': 1,
        })
        self.assertEqual(response.status_code, 400)

    def test_student_cannot_create_section(self):
        self.auth('ls_student_a')
        response = self.client.post('/api/lessons/sections/', {
            'lesson': self.lesson_id, 'title': 'Sneaky section', 'order': 3,
        })
        self.assertEqual(response.status_code, 403)

    def test_other_teacher_cannot_create_section_on_someone_elses_lesson(self):
        self.auth('ls_teacher_a2')
        response = self.client.post('/api/lessons/sections/', {
            'lesson': self.lesson_id, 'title': 'Sneaky section', 'order': 3,
        })
        self.assertEqual(response.status_code, 403)

    def test_admin_can_create_section(self):
        self.auth('ls_admin_a')
        response = self.client.post('/api/lessons/sections/', {
            'lesson': self.lesson_id, 'title': 'Section 3', 'order': 3,
        })
        self.assertEqual(response.status_code, 201)

    def test_school_is_derived_from_lesson_not_client(self):
        self.auth('ls_teacher_a')
        response = self.client.post('/api/lessons/sections/', {
            'lesson': self.lesson_id, 'title': 'Section 3', 'order': 3, 'school': 99999,
        })
        self.assertEqual(response.data['data']['school'], self.school_a.id)

    def test_image_block_without_image_rejected(self):
        self.auth('ls_teacher_a')
        response = self.client.post(f'/api/lessons/sections/{self.section1_id}/content-blocks/', {
            'block_type': 'IMAGE', 'order': 2,
        })
        self.assertEqual(response.status_code, 400)

    def test_text_block_without_content_rejected(self):
        self.auth('ls_teacher_a')
        response = self.client.post(f'/api/lessons/sections/{self.section1_id}/content-blocks/', {
            'block_type': 'TEXT', 'order': 2, 'content': '',
        })
        self.assertEqual(response.status_code, 400)

    def test_image_block_with_attachment_from_different_lesson_rejected(self):
        self.auth('ls_teacher_a')
        other_lesson_id = self.create_lesson(title='Other Lesson').data['data']['id']
        attachment = self.client.post(f'/api/lessons/{other_lesson_id}/attachments/', {
            'file': SimpleUploadedFile('pic.png', b'\x89PNG fake', content_type='image/png'),
        }, format='multipart').data['data']
        response = self.client.post(f'/api/lessons/sections/{self.section1_id}/content-blocks/', {
            'block_type': 'IMAGE', 'order': 2, 'image': attachment['id'],
        })
        self.assertEqual(response.status_code, 400)

    def test_image_block_with_attachment_from_same_lesson_accepted(self):
        self.auth('ls_teacher_a')
        attachment = self.client.post(f'/api/lessons/{self.lesson_id}/attachments/', {
            'file': SimpleUploadedFile('pic.png', b'\x89PNG fake', content_type='image/png'),
        }, format='multipart').data['data']
        response = self.client.post(f'/api/lessons/sections/{self.section1_id}/content-blocks/', {
            'block_type': 'IMAGE', 'order': 2, 'image': attachment['id'],
        })
        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.data['data']['image_reference'])

    def test_mini_quiz_already_exists_rejected(self):
        self.auth('ls_teacher_a')
        response = self.client.post(f'/api/lessons/sections/{self.section1_id}/mini-quiz/', {'passing_score': 80})
        self.assertEqual(response.status_code, 400)

    def test_mini_quiz_default_passing_score_is_70(self):
        self.auth('ls_teacher_a')
        response = self.client.post(f'/api/lessons/sections/{self.section2_id}/mini-quiz/', {})
        self.assertEqual(response.data['data']['passing_score'], 70)

    def test_mini_quiz_creates_its_own_quiz(self):
        quiz = Quiz.objects.get(id=self.mini_quiz_quiz_id)
        self.assertEqual(quiz.school_id, self.school_a.id)
        self.assertEqual(quiz.subject_id, self.subject_a.id)
        self.assertEqual(SectionMiniQuiz.objects.get(section_id=self.section1_id).quiz_id, quiz.id)

class LessonStructureEndpointTests(SectionsTestBase):
    def test_first_section_available_second_locked_for_fresh_student(self):
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/{self.lesson_id}/structure/')
        sections = response.data['data']['sections']
        self.assertEqual(sections[0]['status'], 'AVAILABLE')
        self.assertEqual(sections[1]['status'], 'LOCKED')

    def test_teacher_sees_preview_with_everything_available(self):
        self.auth('ls_teacher_a')
        response = self.client.get(f'/api/lessons/{self.lesson_id}/structure/')
        sections = response.data['data']['sections']
        self.assertTrue(all(s['status'] == 'AVAILABLE' for s in sections))

    def test_lesson_with_no_sections_returns_empty_list(self):
        self.auth('ls_teacher_a')
        flat_lesson_id = self.create_lesson(title='Flat Lesson').data['data']['id']
        self.client.post(f'/api/lessons/{flat_lesson_id}/publish/')
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/{flat_lesson_id}/structure/')
        self.assertEqual(response.data['data']['sections'], [])

    def test_has_mini_quiz_flag_reflects_section_setup(self):
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/{self.lesson_id}/structure/')
        sections = response.data['data']['sections']
        self.assertTrue(sections[0]['has_mini_quiz'])
        self.assertFalse(sections[1]['has_mini_quiz'])

    def test_other_school_student_cannot_view_structure(self):
        self.auth('ls_student_b')
        response = self.client.get(f'/api/lessons/{self.lesson_id}/structure/')
        self.assertEqual(response.status_code, 404)

class SectionContentEndpointTests(SectionsTestBase):
    def test_available_section_content_accessible(self):
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/sections/{self.section1_id}/content/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['data']['content_blocks']), 1)

    def test_locked_section_content_returns_403(self):
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/sections/{self.section2_id}/content/')
        self.assertEqual(response.status_code, 403)

    def test_mini_quiz_questions_never_reveal_is_correct(self):
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/sections/{self.section1_id}/content/')
        options = response.data['data']['mini_quiz']['questions'][0]['options']
        self.assertTrue(all('is_correct' not in opt for opt in options))

    def test_section_with_no_mini_quiz_returns_null(self):
        self.submit(self.section1_id, self.correct_option_id)
        self.auth('ls_student_a')
        response = self.client.get(f'/api/lessons/sections/{self.section2_id}/content/')
        self.assertIsNone(response.data['data']['mini_quiz'])

    def test_teacher_cannot_use_student_facing_content_endpoint(self):
        self.auth('ls_teacher_a')
        response = self.client.get(f'/api/lessons/sections/{self.section1_id}/content/')
        self.assertEqual(response.status_code, 403)

class SubmitSectionQuizTests(SectionsTestBase):
    def test_passing_promotes_an_existing_locked_next_section(self):
        StudentSectionProgress.objects.create(
            school=self.school_a, student=self.student_a, section_id=self.section2_id,
            status=StudentSectionProgress.Status.LOCKED,
        )
        response = self.submit(self.section1_id, self.correct_option_id)
        self.assertTrue(response.data['data']['passed'])
        self.assertTrue(response.data['data']['next_section_unlocked'])
        next_progress = StudentSectionProgress.objects.get(student=self.student_a, section_id=self.section2_id)
        self.assertEqual(next_progress.status, StudentSectionProgress.Status.AVAILABLE)

    def test_correct_answer_passes_and_unlocks_next_section(self):
        response = self.submit(self.section1_id, self.correct_option_id)
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertTrue(data['passed'])
        self.assertEqual(float(data['score']), 100.0)
        self.assertTrue(data['next_section_unlocked'])
        self.assertEqual(data['next_section_id'], self.section2_id)

        progress = StudentSectionProgress.objects.get(student=self.student_a, section_id=self.section1_id)
        self.assertEqual(progress.status, StudentSectionProgress.Status.COMPLETED)
        next_progress = StudentSectionProgress.objects.get(student=self.student_a, section_id=self.section2_id)
        self.assertEqual(next_progress.status, StudentSectionProgress.Status.AVAILABLE)

    def test_wrong_answer_fails_and_keeps_section_available(self):
        response = self.submit(self.section1_id, self.wrong_option_id)
        self.assertEqual(response.status_code, 200)
        data = response.data['data']
        self.assertFalse(data['passed'])
        self.assertEqual(float(data['score']), 0.0)
        self.assertFalse(data['next_section_unlocked'])
        self.assertEqual(len(data['wrong_answers']), 1)
        self.assertEqual(data['wrong_answers'][0]['explanation'], 'Basic addition: 2 + 2 = 4.')

        progress = StudentSectionProgress.objects.get(student=self.student_a, section_id=self.section1_id)
        self.assertEqual(progress.status, StudentSectionProgress.Status.AVAILABLE)
        self.assertFalse(
            StudentSectionProgress.objects.filter(student=self.student_a, section_id=self.section2_id).exists(),
        )

    def test_second_section_is_not_unlocked_until_first_is_passed(self):
        self.submit(self.section1_id, self.wrong_option_id)
        response = self.submit(self.section2_id, self.wrong_option_id)
        self.assertEqual(response.status_code, 403)

    def test_attempts_counter_increments_across_retries(self):
        self.submit(self.section1_id, self.wrong_option_id)
        self.submit(self.section1_id, self.wrong_option_id)
        response = self.submit(self.section1_id, self.correct_option_id)
        self.assertEqual(response.data['data']['attempts'], 3)

    def test_retaking_after_pass_does_not_retrigger_unlock_flag(self):
        self.submit(self.section1_id, self.correct_option_id)
        response = self.submit(self.section1_id, self.correct_option_id)
        self.assertTrue(response.data['data']['passed'])
        self.assertFalse(response.data['data']['next_section_unlocked'])

    def test_best_score_keeps_the_highest_attempt(self):
        self.submit(self.section1_id, self.wrong_option_id)
        self.submit(self.section1_id, self.correct_option_id)
        progress = StudentSectionProgress.objects.get(student=self.student_a, section_id=self.section1_id)
        self.assertEqual(progress.best_score, 100)

    def test_cannot_submit_quiz_for_section_with_no_mini_quiz(self):
        self.submit(self.section1_id, self.correct_option_id)
        self.auth('ls_student_a')
        response = self.client.post(f'/api/lessons/sections/{self.section2_id}/submit-quiz/', {
            'answers': [],
        }, format='json')
        self.assertEqual(response.status_code, 400)

    def test_cannot_submit_quiz_while_mini_quiz_still_draft(self):
        self.auth('ls_teacher_a')
        self.client.post(f'/api/lessons/sections/{self.section2_id}/mini-quiz/', {'passing_score': 70})
        response = self.submit(self.section2_id, self.wrong_option_id)
        self.assertEqual(response.status_code, 403)

    def test_teacher_cannot_submit_quiz(self):
        response = self.submit(self.section1_id, self.correct_option_id, username='ls_teacher_a')
        self.assertEqual(response.status_code, 403)

    def test_unauthenticated_cannot_submit_quiz(self):
        self.client.credentials()
        response = self.client.post(f'/api/lessons/sections/{self.section1_id}/submit-quiz/', {
            'answers': [],
        }, format='json')
        self.assertEqual(response.status_code, 401)

class SectionCrossSchoolIsolationTests(SectionsTestBase):
    def test_other_school_student_cannot_view_content(self):
        self.auth('ls_student_b')
        response = self.client.get(f'/api/lessons/sections/{self.section1_id}/content/')
        self.assertEqual(response.status_code, 404)

    def test_other_school_student_cannot_submit_quiz(self):
        response = self.submit(self.section1_id, self.correct_option_id, username='ls_student_b')
        self.assertEqual(response.status_code, 404)

    def test_other_school_teacher_cannot_manage_sections(self):
        self.auth('ls_teacher_b')
        response = self.client.get(f'/api/lessons/sections/{self.section1_id}/')
        self.assertEqual(response.status_code, 404)
