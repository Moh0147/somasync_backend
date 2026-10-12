import mimetypes
import os

from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.classes.models import SchoolClass
from apps.quizzes.models import Quiz
from apps.quizzes.serializers import SafeQuestionSerializer
from common.mixins import EnvelopeMixin, TenantOwnedViewSetMixin
from common.permissions import (
    IsAdmin, IsOwnerOrSchoolAdmin, IsStudent, IsTeacher, TenantScopedQuerysetMixin, is_system_admin,
)
from common.utils import api_response, compute_file_checksum

from .models import (
    ALLOWED_ATTACHMENT_EXTENSIONS, Lesson, LessonAssignment, LessonAttachment, LessonSection,
    MAX_ATTACHMENT_SIZE_BYTES, SectionContentBlock, SectionMiniQuiz, StudentSectionProgress,
)
from .serializers import (
    CreateSectionMiniQuizSerializer, LessonAssignmentSerializer, LessonAttachmentSerializer, LessonSectionSerializer,
    LessonSerializer, SectionContentBlockSerializer, SectionMiniQuizSerializer, SectionStatusSerializer,
    SubmitSectionQuizSerializer,
)
from .services import ensure_section_accessible, section_statuses_for_student, submit_section_quiz

class LessonViewSet(EnvelopeMixin, TenantOwnedViewSetMixin, viewsets.ModelViewSet):
    """/api/lessons/ — school-owned. A teacher authors their own lessons;
    school/system admins have full oversight (edit or moderate anyone's).
    Visibility: admins see everything in their school; a teacher sees every
    PUBLISHED lesson plus their own regardless of status; a student only
    ever sees PUBLISHED lessons."""

    queryset = Lesson.objects.select_related(
        'school', 'subject', 'class_level', 'topic', 'learning_objective', 'created_by',
    )
    serializer_class = LessonSerializer
    success_messages = {'create': 'Lesson created', 'update': 'Lesson updated'}

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if is_system_admin(user) or user.role == 'ADMIN':
            return queryset
        if user.role == 'TEACHER':
            return queryset.filter(Q(status=Lesson.Status.PUBLISHED) | Q(created_by=user))
        return queryset.filter(status=Lesson.Status.PUBLISHED)

    def get_permissions(self):
        if self.action == 'create':
            return [IsAuthenticated(), (IsAdmin | IsTeacher)()]
        if self.action in ('update', 'partial_update', 'destroy', 'publish', 'archive', 'assign'):
            return [IsAuthenticated(), IsOwnerOrSchoolAdmin()]
        return super().get_permissions()

    def _validate_relationships(self, school, subject, class_level, topic, learning_objective):
        if subject.school_id != school.id:
            raise ValidationError({'subject': 'This subject does not belong to the target school.'})
        if topic:
            if topic.subject_id != subject.id:
                raise ValidationError({'topic': 'This topic does not belong to the selected subject.'})
            if topic.class_level_id != class_level.id:
                raise ValidationError({'topic': 'This topic does not belong to the selected class level.'})
        if learning_objective:
            if not topic:
                raise ValidationError({'learning_objective': 'A learning objective requires a topic to be set.'})
            if learning_objective.topic_id != topic.id:
                raise ValidationError(
                    {'learning_objective': 'This learning objective does not belong to the selected topic.'},
                )

    def perform_create(self, serializer):
        school = self.resolve_create_school(serializer)
        subject = serializer.validated_data.get('subject')
        class_level = serializer.validated_data.get('class_level')
        topic = serializer.validated_data.get('topic')
        learning_objective = serializer.validated_data.get('learning_objective')
        self._validate_relationships(school, subject, class_level, topic, learning_objective)
        serializer.save(school=school, created_by=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.instance
        school = instance.school
        subject = serializer.validated_data.get('subject', instance.subject)
        class_level = serializer.validated_data.get('class_level', instance.class_level)
        topic = serializer.validated_data.get('topic', instance.topic)
        learning_objective = serializer.validated_data.get('learning_objective', instance.learning_objective)
        self._validate_relationships(school, subject, class_level, topic, learning_objective)
        serializer.save(school=school)

    def perform_destroy(self, instance):
        if instance.status != Lesson.Status.DRAFT:
            raise ValidationError('Only a DRAFT lesson can be deleted — archive it instead.')
        instance.delete()

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        lesson = self.get_object()
        if lesson.status == Lesson.Status.PUBLISHED:
            raise ValidationError('This lesson is already published.')
        lesson.status = Lesson.Status.PUBLISHED
        lesson.published_at = timezone.now()
        lesson.save(update_fields=['status', 'published_at', 'updated_at'])
        return api_response(data=self.get_serializer(lesson).data, message='Lesson published')

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        lesson = self.get_object()
        if lesson.status == Lesson.Status.ARCHIVED:
            raise ValidationError('This lesson is already archived.')
        lesson.status = Lesson.Status.ARCHIVED
        lesson.save(update_fields=['status', 'updated_at'])
        return api_response(data=self.get_serializer(lesson).data, message='Lesson archived')

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='assign')
    def assign(self, request, pk=None):
        lesson = self.get_object()

        if request.method == 'GET':
            assignments = LessonAssignment.objects.filter(lesson=lesson).select_related('school_class')
            return api_response(data=LessonAssignmentSerializer(assignments, many=True).data)

        school_class_id = request.data.get('school_class')
        if not school_class_id:
            raise ValidationError({'school_class': 'This field is required.'})
        try:
            school_class = SchoolClass.objects.get(pk=school_class_id)
        except SchoolClass.DoesNotExist:
            raise ValidationError({'school_class': 'No such class.'})

        if school_class.school_id != lesson.school_id:
            raise ValidationError({'school_class': "This class does not belong to the lesson's school."})

        if request.method == 'POST':
            if lesson.status != Lesson.Status.PUBLISHED:
                raise ValidationError('Only a published lesson can be assigned to a class.')
            _, created = LessonAssignment.objects.get_or_create(
                lesson=lesson, school_class=school_class, defaults={'assigned_by': request.user},
            )
            if not created:
                raise ValidationError('This lesson is already assigned to this class.')
            return api_response(message='Lesson assigned to class', status=status.HTTP_201_CREATED)

        deleted, _ = LessonAssignment.objects.filter(lesson=lesson, school_class=school_class).delete()
        if not deleted:
            raise ValidationError('This lesson is not assigned to this class.')
        return api_response(message='Lesson unassigned from class')

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='attachments', permission_classes=[IsAuthenticated])
    def attachments(self, request, pk=None):
        lesson = self.get_object()

        if request.method == 'GET':
            data = LessonAttachmentSerializer(lesson.attachments.all(), many=True, context={'request': request}).data
            return api_response(data=data)

        if not self._can_edit(request.user, lesson):
            raise PermissionDenied("Only this lesson's editor may manage its attachments.")

        if request.method == 'DELETE':
            attachment_id = request.data.get('attachment_id')
            deleted, _ = LessonAttachment.objects.filter(id=attachment_id, lesson=lesson).delete()
            if not deleted:
                raise ValidationError({'attachment_id': 'No such attachment on this lesson.'})
            return api_response(message='Attachment removed')

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            raise ValidationError({'file': 'This field is required.'})

        ext = os.path.splitext(uploaded_file.name)[1].lower()
        if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
            raise ValidationError({'file': f'File type "{ext or "unknown"}" is not allowed.'})
        if uploaded_file.size > MAX_ATTACHMENT_SIZE_BYTES:
            raise ValidationError({'file': f'File exceeds the {MAX_ATTACHMENT_SIZE_BYTES // (1024 * 1024)}MB limit.'})

        checksum = compute_file_checksum(uploaded_file)

        next_version = (
            LessonAttachment.objects.filter(lesson=lesson, file_name=uploaded_file.name)
            .aggregate(Max('version'))['version__max'] or 0
        ) + 1

        attachment = LessonAttachment.objects.create(
            lesson=lesson,
            file=uploaded_file,
            file_name=uploaded_file.name,
            mime_type=mimetypes.guess_type(uploaded_file.name)[0] or 'application/octet-stream',
            file_size=uploaded_file.size,
            checksum=checksum,
            version=next_version,
        )
        return api_response(
            data=LessonAttachmentSerializer(attachment, context={'request': request}).data,
            message='Attachment uploaded', status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get'])
    def structure(self, request, pk=None):
        """GET /api/lessons/{id}/structure/ — the sectioned view of this
        lesson: ordered sections with this student's per-section status
        (LOCKED/AVAILABLE/COMPLETED). A lesson with zero sections just
        returns an empty list -- the flat `content`/attachments view is
        still the whole lesson in that case, unaffected by any of this.

        Teachers/admins get a preview (every section shown AVAILABLE) —
        they're not the learner here, so real per-student gating doesn't
        apply to them.
        """
        lesson = self.get_object()
        user = request.user

        if is_system_admin(user) or user.role in ('ADMIN', 'TEACHER'):
            rows = [
                {'section': s, 'status': StudentSectionProgress.Status.AVAILABLE, 'best_score': None, 'attempts': 0}
                for s in lesson.sections.select_related('mini_quiz').order_by('order')
            ]
        else:
            rows = section_statuses_for_student(lesson, user)

        return api_response(data={
            'lesson_id': lesson.id, 'title': lesson.title,
            'sections': SectionStatusSerializer(rows, many=True).data,
        })

    def _can_edit(self, user, lesson):
        if is_system_admin(user):
            return True
        if user.role == 'ADMIN' and user.school_id == lesson.school_id:
            return True
        return lesson.created_by_id == user.id

class LessonSectionViewSet(EnvelopeMixin, TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    """/api/lessons/sections/?lesson=<id> — authoring (create/update/
    destroy/content-blocks/mini-quiz) is the lesson's own editor (its
    teacher, or an admin); base list/retrieve is also editor-only, since it
    has no gating logic of its own. Students never hit this ViewSet's
    plain CRUD — they go through `content`/`submit-quiz`, which enforce
    section-unlock status themselves.
    """

    queryset = LessonSection.objects.select_related('school', 'lesson', 'mini_quiz').prefetch_related('content_blocks')
    serializer_class = LessonSectionSerializer
    success_messages = {'create': 'Section created', 'update': 'Section updated'}

    def get_queryset(self):
        queryset = super().get_queryset()
        lesson_id = self.request.query_params.get('lesson')
        if lesson_id:
            queryset = queryset.filter(lesson_id=lesson_id)
        user = self.request.user
        if is_system_admin(user) or user.role == 'ADMIN':
            return queryset
        if user.role == 'TEACHER':
            return queryset.filter(lesson__created_by=user)
        return queryset.filter(lesson__status=Lesson.Status.PUBLISHED)

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update', 'destroy', 'content_blocks', 'mini_quiz'):
            return [IsAuthenticated(), (IsAdmin | IsTeacher)()]
        if self.action in ('content', 'submit_quiz'):
            return [IsAuthenticated(), IsStudent()]
        return [IsAuthenticated()]

    def _can_edit(self, user, lesson):
        if is_system_admin(user):
            return True
        if user.role == 'ADMIN' and user.school_id == lesson.school_id:
            return True
        return lesson.created_by_id == user.id

    def perform_create(self, serializer):
        lesson = serializer.validated_data.get('lesson')
        if not lesson:
            raise ValidationError({'lesson': 'This field is required.'})
        if not self._can_edit(self.request.user, lesson):
            raise PermissionDenied("Only this lesson's editor may manage its sections.")
        serializer.save(school=lesson.school)

    def perform_update(self, serializer):
        if not self._can_edit(self.request.user, serializer.instance.lesson):
            raise PermissionDenied("Only this lesson's editor may manage its sections.")
        serializer.save()

    def perform_destroy(self, instance):
        if not self._can_edit(self.request.user, instance.lesson):
            raise PermissionDenied("Only this lesson's editor may manage its sections.")
        instance.delete()

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='content-blocks')
    def content_blocks(self, request, pk=None):
        section = self.get_object()

        if request.method == 'GET':
            data = SectionContentBlockSerializer(
                section.content_blocks.all(), context={'request': request}, many=True,
            ).data
            return api_response(data=data)

        if not self._can_edit(request.user, section.lesson):
            raise PermissionDenied("Only this lesson's editor may manage its sections.")

        if request.method == 'DELETE':
            block_id = request.data.get('block_id')
            deleted, _ = SectionContentBlock.objects.filter(id=block_id, section=section).delete()
            if not deleted:
                raise ValidationError({'block_id': 'No such content block on this section.'})
            return api_response(message='Content block removed')

        serializer = SectionContentBlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        block_type = data['block_type']
        image = data.get('image')
        content = data.get('content', '')

        if block_type == SectionContentBlock.BlockType.IMAGE:
            if not image:
                raise ValidationError({'image': 'An IMAGE block requires an image attachment.'})
            if image.lesson_id != section.lesson_id:
                raise ValidationError({'image': 'The image must be an attachment of the same lesson.'})
        elif not content:
            raise ValidationError({'content': f'A {block_type} block requires content text.'})

        block = SectionContentBlock.objects.create(school=section.school, section=section, **data)
        return api_response(
            data=SectionContentBlockSerializer(block, context={'request': request}).data,
            message='Content block added', status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get', 'post'], url_path='mini-quiz')
    def mini_quiz(self, request, pk=None):
        section = self.get_object()
        existing = getattr(section, 'mini_quiz', None)

        if request.method == 'GET':
            if existing is None:
                raise ValidationError('This section has no mini-quiz yet.')
            return api_response(data=SectionMiniQuizSerializer(existing).data)

        if not self._can_edit(request.user, section.lesson):
            raise PermissionDenied("Only this lesson's editor may manage its sections.")
        if existing is not None:
            raise ValidationError('This section already has a mini-quiz.')

        serializer = CreateSectionMiniQuizSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        quiz = Quiz.objects.create(
            school=section.school, subject=section.lesson.subject, class_level=section.lesson.class_level,
            title=f'{section.title} - Mini Quiz', created_by=request.user,
        )
        created_mini_quiz = SectionMiniQuiz.objects.create(
            school=section.school, section=section, quiz=quiz,
            passing_score=serializer.validated_data['passing_score'],
        )
        return api_response(
            data=SectionMiniQuizSerializer(created_mini_quiz).data,
            message="Mini-quiz created — add questions via /api/quizzes/questions/, then publish the quiz",
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get'])
    def content(self, request, pk=None):
        """GET /api/lessons/sections/{id}/content/ — content blocks plus
        the mini-quiz's questions with NO answer key (SafeQuestionSerializer).
        403s if the section is still LOCKED for this student."""
        section = self.get_object()
        ensure_section_accessible(section, request.user)

        blocks = SectionContentBlockSerializer(
            section.content_blocks.all(), context={'request': request}, many=True,
        ).data

        mini_quiz_data = None
        mini_quiz = getattr(section, 'mini_quiz', None)
        if mini_quiz is not None:
            questions = mini_quiz.quiz.questions.prefetch_related('options', 'pairs').order_by('order')
            mini_quiz_data = {
                'passing_score': mini_quiz.passing_score,
                'questions': SafeQuestionSerializer(questions, many=True).data,
            }

        return api_response(data={'content_blocks': blocks, 'mini_quiz': mini_quiz_data})

    @action(detail=True, methods=['post'], url_path='submit-quiz')
    def submit_quiz(self, request, pk=None):
        """POST /api/lessons/sections/{id}/submit-quiz/ — deterministic
        marking only (QuizMarkingService), same as every other quiz in
        this system. Passing unlocks the next section; failing returns
        per-question explanations for whatever was answered wrong."""
        section = self.get_object()
        serializer = SubmitSectionQuizSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = submit_section_quiz(request.user, section, serializer.validated_data['answers'])
        return api_response(data=result, message='Section completed' if result['passed'] else 'Not passed yet')
