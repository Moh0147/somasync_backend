from django.db.models import Q
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from apps.classes.models import SchoolClass
from common.mixins import EnvelopeMixin, TenantOwnedViewSetMixin
from common.permissions import (
    IsAdmin, IsOwnerOrSchoolAdmin, IsStudent, IsTeacher, TenantScopedQuerysetMixin, is_system_admin,
)
from common.utils import api_response

from .models import (
    MatchingPair, Question, QuestionOption, Quiz, QuizAssignment, StudentQuizAttempt,
)
from .services import (
    QuizMarkingService, save_quiz_answer, sync_quiz_attempt, validate_quiz_for_attempt,
)
from .serializers import (
    AnswerInputSerializer, MatchingPairSerializer, QuestionOptionSerializer, QuestionSerializer,
    QuizAssignmentSerializer, QuizSerializer, StartAttemptSerializer, StudentQuizAttemptSerializer,
    SyncAttemptSerializer, SafeQuestionSerializer,
)
from .serializers import SafeQuestionSerializer


class QuizViewSet(EnvelopeMixin, TenantOwnedViewSetMixin, viewsets.ModelViewSet):
    """/api/quizzes/ — school-owned. A teacher authors their own quizzes;
    school/system admins have full oversight. Visibility mirrors Lesson:
    admins see everything in their school; a teacher sees every PUBLISHED
    quiz plus their own; a student only ever sees PUBLISHED quizzes.

    Normal quiz marking never uses AI — deterministic marking is
    QuizMarkingService, Phase 9."""

    queryset = Quiz.objects.select_related(
        'school', 'subject', 'class_level', 'topic', 'learning_objective', 'created_by',
    )
    serializer_class = QuizSerializer
    success_messages = {'create': 'Quiz created', 'update': 'Quiz updated'}

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if is_system_admin(user) or user.role == 'ADMIN':
            return queryset
        if user.role == 'TEACHER':
            return queryset.filter(Q(status=Quiz.Status.PUBLISHED) | Q(created_by=user))
        # Students see published quizzes; access to questions/attempts is
        # checked separately by validate_quiz_for_attempt, which also
        # verifies class assignment.
        return queryset.filter(status=Quiz.Status.PUBLISHED)

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
        if instance.status != Quiz.Status.DRAFT:
            raise ValidationError(
                'Only a DRAFT quiz can be edited — a published one may already be downloaded offline.',
            )
        school = instance.school
        subject = serializer.validated_data.get('subject', instance.subject)
        class_level = serializer.validated_data.get('class_level', instance.class_level)
        topic = serializer.validated_data.get('topic', instance.topic)
        learning_objective = serializer.validated_data.get('learning_objective', instance.learning_objective)
        self._validate_relationships(school, subject, class_level, topic, learning_objective)
        serializer.save(school=school)

    def perform_destroy(self, instance):
        if instance.status != Quiz.Status.DRAFT:
            raise ValidationError('Only a DRAFT quiz can be deleted — archive it instead.')
        instance.delete()

    def _validate_publishable(self, quiz):
        questions = list(quiz.questions.prefetch_related('options', 'pairs'))
        if not questions:
            raise ValidationError('A quiz must have at least one question before it can be published.')

        errors = []
        for q in questions:
            if q.question_type == Question.Type.MATCHING:
                if q.pairs.count() < 2:
                    errors.append(f'Question {q.id}: a matching question needs at least 2 pairs.')
                continue

            options = list(q.options.all())
            correct_count = sum(1 for o in options if o.is_correct)
            if q.question_type == Question.Type.TRUE_FALSE and len(options) != 2:
                errors.append(f'Question {q.id}: a true/false question needs exactly 2 options.')
            elif q.question_type == Question.Type.MCQ and len(options) < 2:
                errors.append(f'Question {q.id}: a multiple choice question needs at least 2 options.')
            if correct_count != 1:
                errors.append(f'Question {q.id}: exactly one option must be marked correct.')

        if errors:
            raise ValidationError({'questions': errors})

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        quiz = self.get_object()
        if quiz.status == Quiz.Status.PUBLISHED:
            raise ValidationError('This quiz is already published.')
        self._validate_publishable(quiz)
        quiz.status = Quiz.Status.PUBLISHED
        quiz.published_at = timezone.now()
        quiz.save(update_fields=['status', 'published_at', 'updated_at'])
        return api_response(data=self.get_serializer(quiz).data, message='Quiz published')

    @action(detail=True, methods=['get'], url_path='student-questions', permission_classes=[IsAuthenticated, IsStudent])
    def student_questions(self, request, pk=None):
        """Answer-key-free question payload for an assigned published quiz."""
        quiz = self.get_object()
        validate_quiz_for_attempt(quiz.id, request.user)
        questions = quiz.questions.prefetch_related('options', 'pairs').order_by('order')
        return api_response(data=SafeQuestionSerializer(questions, many=True).data)

    @action(detail=True, methods=['get'], url_path='student-questions', permission_classes=[IsAuthenticated, IsStudent])
    def student_questions(self, request, pk=None):
        """Return answer-key-free questions for an assigned published quiz."""
        quiz = self.get_object()
        validate_quiz_for_attempt(quiz.id, request.user)
        questions = quiz.questions.prefetch_related('options', 'pairs').order_by('order')
        return api_response(data=SafeQuestionSerializer(questions, many=True).data)

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        quiz = self.get_object()
        if quiz.status == Quiz.Status.ARCHIVED:
            raise ValidationError('This quiz is already archived.')
        quiz.status = Quiz.Status.ARCHIVED
        quiz.save(update_fields=['status', 'updated_at'])
        return api_response(data=self.get_serializer(quiz).data, message='Quiz archived')

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='assign')
    def assign(self, request, pk=None):
        quiz = self.get_object()

        if request.method == 'GET':
            assignments = QuizAssignment.objects.filter(quiz=quiz).select_related('school_class')
            return api_response(data=QuizAssignmentSerializer(assignments, many=True).data)

        school_class_id = request.data.get('school_class')
        if not school_class_id:
            raise ValidationError({'school_class': 'This field is required.'})
        try:
            school_class = SchoolClass.objects.get(pk=school_class_id)
        except SchoolClass.DoesNotExist:
            raise ValidationError({'school_class': 'No such class.'})

        if school_class.school_id != quiz.school_id:
            raise ValidationError({'school_class': "This class does not belong to the quiz's school."})

        if request.method == 'POST':
            if quiz.status != Quiz.Status.PUBLISHED:
                raise ValidationError('Only a published quiz can be assigned to a class.')
            _, created = QuizAssignment.objects.get_or_create(
                quiz=quiz, school_class=school_class, defaults={'assigned_by': request.user},
            )
            if not created:
                raise ValidationError('This quiz is already assigned to this class.')
            return api_response(message='Quiz assigned to class', status=status.HTTP_201_CREATED)

        deleted, _ = QuizAssignment.objects.filter(quiz=quiz, school_class=school_class).delete()
        if not deleted:
            raise ValidationError('This quiz is not assigned to this class.')
        return api_response(message='Quiz unassigned from class')


class QuestionViewSet(EnvelopeMixin, TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    """/api/quizzes/questions/?quiz=<id> — authoring-only. NEVER exposed to
    students in this phase: it reveals is_correct / the answer key. A
    student-safe, answer-hidden view for actually taking a quiz belongs to
    Phase 8 (StudentQuizAttempt), not here."""

    queryset = Question.objects.select_related('school', 'quiz').prefetch_related('options', 'pairs')
    serializer_class = QuestionSerializer
    success_messages = {'create': 'Question created', 'update': 'Question updated'}

    def get_queryset(self):
        queryset = super().get_queryset()
        quiz_id = self.request.query_params.get('quiz')
        if quiz_id:
            queryset = queryset.filter(quiz_id=quiz_id)
        user = self.request.user
        if is_system_admin(user) or user.role == 'ADMIN':
            return queryset
        if user.role == 'TEACHER':
            return queryset.filter(quiz__created_by=user)
        return queryset.none()

    def get_permissions(self):
        if self.action == 'create':
            return [IsAuthenticated(), (IsAdmin | IsTeacher)()]
        if self.action in ('update', 'partial_update', 'destroy'):
            return [IsAuthenticated(), IsOwnerOrSchoolAdmin()]
        return super().get_permissions()

    def _require_draft_quiz(self, quiz):
        if quiz.status != Quiz.Status.DRAFT:
            raise ValidationError('Questions can only be modified while the quiz is in DRAFT status.')

    def perform_create(self, serializer):
        quiz = serializer.validated_data.get('quiz')
        user = self.request.user
        can_edit = (
            is_system_admin(user)
            or (user.role == 'ADMIN' and user.school_id == quiz.school_id)
            or quiz.created_by_id == user.id
        )
        if not can_edit:
            raise PermissionDenied("Only this quiz's editor may add questions to it.")
        self._require_draft_quiz(quiz)
        serializer.save(school=quiz.school, created_by=quiz.created_by)

    def perform_update(self, serializer):
        self._require_draft_quiz(serializer.instance.quiz)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_draft_quiz(instance.quiz)
        instance.delete()

    def _can_edit(self, request, question):
        return IsOwnerOrSchoolAdmin().has_object_permission(request, self, question)

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='options', permission_classes=[IsAuthenticated])
    def options(self, request, pk=None):
        question = self.get_object()

        if request.method == 'GET':
            return api_response(data=QuestionOptionSerializer(question.options.all(), many=True).data)

        if not self._can_edit(request, question):
            raise PermissionDenied("Only this quiz's editor may manage its questions.")
        self._require_draft_quiz(question.quiz)

        if question.question_type == Question.Type.MATCHING:
            raise ValidationError('A matching question does not take options — use pairs instead.')

        if request.method == 'DELETE':
            option_id = request.data.get('option_id')
            deleted, _ = QuestionOption.objects.filter(id=option_id, question=question).delete()
            if not deleted:
                raise ValidationError({'option_id': 'No such option on this question.'})
            return api_response(message='Option removed')

        if question.question_type == Question.Type.TRUE_FALSE and question.options.count() >= 2:
            raise ValidationError('A true/false question can only have 2 options.')

        serializer = QuestionOptionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        option = QuestionOption.objects.create(question=question, **serializer.validated_data)
        return api_response(
            data=QuestionOptionSerializer(option).data, message='Option added', status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='pairs', permission_classes=[IsAuthenticated])
    def pairs(self, request, pk=None):
        question = self.get_object()

        if request.method == 'GET':
            return api_response(data=MatchingPairSerializer(question.pairs.all(), many=True).data)

        if not self._can_edit(request, question):
            raise PermissionDenied("Only this quiz's editor may manage its questions.")
        self._require_draft_quiz(question.quiz)

        if question.question_type != Question.Type.MATCHING:
            raise ValidationError('Only a matching question takes pairs — use options instead.')

        if request.method == 'DELETE':
            pair_id = request.data.get('pair_id')
            deleted, _ = MatchingPair.objects.filter(id=pair_id, question=question).delete()
            if not deleted:
                raise ValidationError({'pair_id': 'No such pair on this question.'})
            return api_response(message='Pair removed')

        serializer = MatchingPairSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pair = MatchingPair.objects.create(question=question, **serializer.validated_data)
        return api_response(
            data=MatchingPairSerializer(pair).data, message='Pair added', status=status.HTTP_201_CREATED,
        )


class StudentQuizAttemptViewSet(
    EnvelopeMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet,
):
    """/api/quiz-attempts/ — designed to work fully offline. A student
    starts/answers/submits an attempt (works live), or replays an entire
    offline session in one `sync` call (works after reconnecting) — both
    paths converge on the same idempotency guarantee: `client_attempt_id`
    (device-generated) means syncing/retrying never creates duplicates.

    Read access: a student sees only their own attempts; a teacher sees
    attempts for quizzes they created; an admin sees their school's
    attempts; a system admin sees everything.
    """

    queryset = StudentQuizAttempt.objects.select_related('school', 'quiz', 'student').prefetch_related('answers')
    serializer_class = StudentQuizAttemptSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'client_attempt_id'
    lookup_url_kwarg = 'client_attempt_id'
    lookup_value_regex = '[^/]+'

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if is_system_admin(user):
            return queryset
        if user.role == 'ADMIN':
            return queryset.filter(school_id=user.school_id)
        if user.role == 'TEACHER':
            return queryset.filter(quiz__created_by=user)
        if user.role == 'STUDENT':
            return queryset.filter(student=user)
        return queryset.none()

    # --- live flow ---

    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated, IsStudent])
    def start(self, request):
        serializer = StartAttemptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        client_attempt_id = serializer.validated_data['client_attempt_id']

        existing = StudentQuizAttempt.objects.filter(client_attempt_id=client_attempt_id).first()
        if existing:
            if existing.student_id != request.user.id:
                raise ValidationError({'client_attempt_id': 'This client_attempt_id is already in use.'})
            return api_response(data=self.get_serializer(existing).data, message='Attempt already exists')

        quiz = validate_quiz_for_attempt(serializer.validated_data['quiz'], request.user)
        attempt = StudentQuizAttempt.objects.create(
            school=quiz.school, quiz=quiz, student=request.user,
            client_attempt_id=client_attempt_id, status=StudentQuizAttempt.Status.IN_PROGRESS,
        )
        return api_response(
            data=self.get_serializer(attempt).data, message='Attempt started', status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def answer(self, request, client_attempt_id=None):
        attempt = self.get_object()
        if attempt.student_id != request.user.id:
            raise PermissionDenied('You may only answer your own attempt.')
        if attempt.status not in (StudentQuizAttempt.Status.IN_PROGRESS, StudentQuizAttempt.Status.SYNC_PENDING):
            raise ValidationError('This attempt is no longer accepting answers.')

        serializer = AnswerInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        save_quiz_answer(attempt, serializer.validated_data)

        attempt.refresh_from_db()
        return api_response(data=self.get_serializer(attempt).data, message='Answer recorded')

    @action(detail=True, methods=['post'])
    def submit(self, request, client_attempt_id=None):
        attempt = self.get_object()
        if attempt.student_id != request.user.id:
            raise PermissionDenied('You may only submit your own attempt.')

        if attempt.status in (StudentQuizAttempt.Status.SUBMITTED, StudentQuizAttempt.Status.SYNCED):
            return api_response(data=self.get_serializer(attempt).data, message='Attempt already submitted')

        attempt.status = StudentQuizAttempt.Status.SUBMITTED
        attempt.submitted_at = timezone.now()
        attempt.save(update_fields=['status', 'submitted_at', 'updated_at'])
        attempt = QuizMarkingService.mark_attempt(attempt)
        return api_response(data=self.get_serializer(attempt).data, message='Attempt submitted')

    # --- offline batch sync ---

    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated, IsStudent])
    def sync(self, request):
        serializer = SyncAttemptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        was_new = not StudentQuizAttempt.objects.filter(client_attempt_id=data['client_attempt_id']).exists()
        attempt = sync_quiz_attempt(request.user, data)

        if attempt.status == StudentQuizAttempt.Status.FAILED:
            return api_response(
                data=self.get_serializer(attempt).data, message='Sync failed', status=status.HTTP_400_BAD_REQUEST,
            )
        return api_response(
            data=self.get_serializer(attempt).data, message='Attempt synced',
            status=status.HTTP_201_CREATED if was_new else status.HTTP_200_OK,
        )
