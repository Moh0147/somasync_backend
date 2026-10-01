import mimetypes
import os
import re
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth import get_user_model
from django.db.models import Max, Sum
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from common.mixins import EnvelopeMixin, TenantOwnedViewSetMixin
from common.permissions import (
    IsAdmin, IsOwnerOrSchoolAdmin, IsStudent, IsTeacher, TenantScopedQuerysetMixin, is_system_admin,
)
from common.utils import api_response, compute_file_checksum

from .ai_marking_service import AIServiceError, ExamAIMarkingService
from .models import (
    ALLOWED_SCRIPT_EXTENSIONS, Exam, ExamCandidate, ExamQuestion, ExamScript, MAX_SCRIPT_SIZE_BYTES,
    MarkingSchemeDocument, MarkingSchemeItem, StudentExamAnswer, StudentExamAttempt,
)
from .services import ExamMarkingService
from .ocr_service import extract_script, map_answers
from .serializers import (
    ExamCandidateInputSerializer, ExamCandidateSerializer, ExamQuestionSerializer, ExamResultSerializer,
    ExamScriptSerializer, ExamSerializer, MarkAnswerInputSerializer, MarkingSchemeItemSerializer,
    StudentExamAnswerSerializer, StudentExamAttemptSerializer,
)

User = get_user_model()


class ExamViewSet(EnvelopeMixin, TenantOwnedViewSetMixin, viewsets.ModelViewSet):
    """/api/exams/ — school-owned. A teacher authors their own exams;
    school/system admins have full oversight. Authoring-only: exams are
    administered on paper, so there's no student-facing visibility here
    the way Quiz has (SomaSync assists marking a physical exam in later
    phases, it never replaces the sitting itself)."""

    queryset = Exam.objects.select_related('school', 'subject', 'class_level', 'created_by')
    serializer_class = ExamSerializer
    success_messages = {'create': 'Exam created', 'update': 'Exam updated'}

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if is_system_admin(user) or user.role == 'ADMIN':
            return queryset
        if user.role == 'TEACHER':
            return queryset.filter(created_by=user)
        return queryset.none()

    def get_permissions(self):
        if self.action == 'create':
            return [IsAuthenticated(), (IsAdmin | IsTeacher)()]
        if self.action in (
            'update', 'partial_update', 'destroy', 'publish', 'close', 'candidates', 'scripts', 'attempts',
            'start_marking', 'publish_results',
        ):
            return [IsAuthenticated(), IsOwnerOrSchoolAdmin()]
        return super().get_permissions()

    def _validate_unique_title(self, school, title, academic_year, exclude_pk=None):
        qs = Exam.objects.filter(school=school, title=title, academic_year=academic_year)
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        if qs.exists():
            raise ValidationError({'title': 'An exam with this title already exists for this school and year.'})

    def _validate_relationships(self, school, subject):
        if subject.school_id != school.id:
            raise ValidationError({'subject': 'This subject does not belong to the target school.'})

    def perform_create(self, serializer):
        school = self.resolve_create_school(serializer)
        subject = serializer.validated_data.get('subject')
        self._validate_relationships(school, subject)
        self._validate_unique_title(
            school, serializer.validated_data.get('title'), serializer.validated_data.get('academic_year'),
        )
        serializer.save(school=school, created_by=self.request.user)

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.status != Exam.Status.DRAFT:
            raise ValidationError('Only a DRAFT exam can be edited.')
        school = instance.school
        subject = serializer.validated_data.get('subject', instance.subject)
        title = serializer.validated_data.get('title', instance.title)
        academic_year = serializer.validated_data.get('academic_year', instance.academic_year)
        self._validate_relationships(school, subject)
        self._validate_unique_title(school, title, academic_year, exclude_pk=instance.pk)
        serializer.save(school=school)

    def perform_destroy(self, instance):
        if instance.status != Exam.Status.DRAFT:
            raise ValidationError('Only a DRAFT exam can be deleted.')
        instance.delete()

    def _validate_publishable(self, exam):
        questions = list(exam.questions.prefetch_related('marking_scheme_items'))
        if not questions:
            raise ValidationError('An exam must have at least one question before it can be published.')

        errors = []
        for question in questions:
            total = sum(item.marks for item in question.marking_scheme_items.all())
            if total != question.max_marks:
                errors.append(
                    f'Question {question.question_number}: marking scheme totals {total}, '
                    f'expected {question.max_marks}.',
                )
        if errors:
            raise ValidationError({'questions': errors})

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        exam = self.get_object()
        if exam.status != Exam.Status.DRAFT:
            raise ValidationError('Only a DRAFT exam can be published.')
        self._validate_publishable(exam)
        exam.status = Exam.Status.PUBLISHED
        exam.published_at = timezone.now()
        exam.save(update_fields=['status', 'published_at', 'updated_at'])
        return api_response(data=self.get_serializer(exam).data, message='Exam published')

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        exam = self.get_object()
        if exam.status != Exam.Status.PUBLISHED:
            raise ValidationError('Only a PUBLISHED exam can be closed.')
        exam.status = Exam.Status.CLOSED
        exam.save(update_fields=['status', 'updated_at'])
        return api_response(data=self.get_serializer(exam).data, message='Exam closed')

    @action(detail=True, methods=['post'], url_path='start-marking')
    def start_marking(self, request, pk=None):
        exam = self.get_object()
        if exam.status != Exam.Status.CLOSED:
            raise ValidationError('Only a CLOSED exam can move to MARKING.')
        exam.status = Exam.Status.MARKING
        exam.save(update_fields=['status', 'updated_at'])
        return api_response(data=self.get_serializer(exam).data, message='Marking started')

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='candidates')
    def candidates(self, request, pk=None):
        exam = self.get_object()

        if request.method == 'GET':
            return api_response(
                data=ExamCandidateSerializer(exam.candidates.select_related('student'), many=True).data,
            )

        if request.method == 'DELETE':
            candidate_number = request.data.get('candidate_number')
            deleted, _ = ExamCandidate.objects.filter(exam=exam, candidate_number=candidate_number).delete()
            if not deleted:
                raise ValidationError({'candidate_number': 'No such candidate on this exam.'})
            return api_response(message='Candidate removed')

        serializer = ExamCandidateInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student_id = serializer.validated_data['student']
        candidate_number = serializer.validated_data['candidate_number']

        try:
            student = User.objects.get(pk=student_id)
        except User.DoesNotExist:
            raise ValidationError({'student': 'No such user.'})
        if student.role != User.Role.STUDENT:
            raise ValidationError({'student': 'User must have role=STUDENT.'})
        if student.school_id != exam.school_id:
            raise ValidationError({'student': "Student does not belong to this exam's school."})
        if ExamCandidate.objects.filter(exam=exam, candidate_number=candidate_number).exists():
            raise ValidationError({'candidate_number': 'This candidate number is already used in this exam.'})
        if ExamCandidate.objects.filter(exam=exam, student=student).exists():
            raise ValidationError({'student': 'This student is already registered for this exam.'})

        candidate = ExamCandidate.objects.create(exam=exam, student=student, candidate_number=candidate_number)
        return api_response(
            data=ExamCandidateSerializer(candidate).data, message='Candidate registered',
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'], url_path='exam-paper/upload')
    def upload_exam_paper(self, request, pk=None):
        exam = self.get_object()
        if exam.status != Exam.Status.DRAFT:
            raise ValidationError('Exam papers can only be uploaded while the exam is in DRAFT.')
        uploaded = request.FILES.get('file')
        if not uploaded:
            raise ValidationError({'file': 'This field is required.'})
        try:
            text, _ = extract_script(uploaded.file)
        except Exception as exc:
            raise ValidationError({'file': str(exc)})
        document = MarkingSchemeDocument.objects.create(
            exam=exam, file=uploaded, file_name=uploaded.name, extracted_text=text,
            source_type='EXAM_PAPER', uploaded_by=request.user,
        )
        # Only import explicit question headings. This avoids mistaking page
        # headers, durations, table cells and footer numbers for questions.
        created = []
        existing = {q.question_number for q in exam.questions.all()}
        heading = re.compile(r'(?im)^\s*question\s+(\d+(?:\s*\([a-z]\))?)\s*(?:\((\d+(?:\.\d+)?)\s*marks?\))?\s*$')
        matches = list(heading.finditer(text))
        for index, match in enumerate(matches):
            number = re.sub(r'\s+', '', match.group(1))
            if number in existing:
                continue
            body = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
            body_lines = [line.strip() for line in body.splitlines() if line.strip()]
            question_text = next((line for line in body_lines if not re.match(r'^(?:item|point|answer|marks|total for question)', line, re.I)), '')
            question = ExamQuestion.objects.create(
                school=exam.school, exam=exam, created_by=exam.created_by,
                question_number=number, question_text=question_text[:10000] or f'Imported question {number} — review',
                question_type=ExamQuestion.Type.SUBJECTIVE, max_marks=int(float(match.group(2) or 1)),
                order=exam.questions.count() + 1,
            )
            existing.add(number); created.append(question.id)
        return api_response(data={'id': document.id, 'file_name': document.file_name, 'extracted_text': text, 'created_question_ids': created}, message='Exam paper extracted. Review imported questions before publishing.', status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='marking-scheme/upload')
    def upload_marking_scheme(self, request, pk=None):
        exam = self.get_object()
        if exam.status != Exam.Status.DRAFT:
            raise ValidationError('Marking schemes can only be uploaded while the exam is in DRAFT.')
        uploaded = request.FILES.get('file')
        if not uploaded:
            raise ValidationError({'file': 'This field is required.'})
        try:
            text, _ = extract_script(uploaded.file)
        except Exception as exc:
            raise ValidationError({'file': str(exc)})
        document = MarkingSchemeDocument.objects.create(
            exam=exam, file=uploaded, file_name=uploaded.name, extracted_text=text,
            source_type='MARKING_SCHEME', uploaded_by=request.user,
        )
        created_questions, created_items = [], []
        questions = {q.question_number: q for q in exam.questions.all()}
        heading = re.compile(r'(?im)^\s*question\s+(\d+(?:\s*\([a-z]\))?)\s*\((\d+(?:\.\d+)?)\s*marks?\)')
        matches = list(heading.finditer(text))
        for index, match in enumerate(matches):
            number = re.sub(r'\s+', '', match.group(1))
            max_marks = int(float(match.group(2)))
            question = questions.get(number)
            if not question:
                question = ExamQuestion.objects.create(
                    school=exam.school, exam=exam, created_by=exam.created_by,
                    question_number=number, question_text=f'Imported question {number} — review question text',
                    question_type=ExamQuestion.Type.SUBJECTIVE, max_marks=max_marks, order=exam.questions.count() + 1,
                )
                questions[number] = question
                created_questions.append(question.id)
            elif question.max_marks == 1 and max_marks > 1:
                question.max_marks = max_marks
                question.save(update_fields=['max_marks', 'updated_at'])
            block = text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)]
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            # Extract numbered marking points whose following numeric line is
            # the awarded mark. This handles the NEC table layout produced by
            # PDF text extraction without trusting page/table coordinates.
            for point_index, line in enumerate(lines):
                if not re.fullmatch(r'\d+', line):
                    continue
                point_lines = []
                mark = None
                for candidate in lines[point_index + 1:point_index + 12]:
                    if re.fullmatch(r'\d+(?:\.\d+)?', candidate):
                        mark = float(candidate)
                        break
                    if re.match(r'^(?:total for question|section|question\s+\d+)', candidate, re.I):
                        break
                    point_lines.append(candidate)
                if not point_lines or mark is None or mark <= 0:
                    continue
                criterion = ' '.join(point_lines)
                if len(criterion) > 1000:
                    criterion = criterion[:1000]
                item = MarkingSchemeItem.objects.create(
                    exam_question=question, criterion=criterion, marks=Decimal(str(mark)),
                    order=question.marking_scheme_items.count() + 1,
                )
                created_items.append(item.id)
        return api_response(data={'id': document.id, 'file_name': document.file_name, 'extracted_text': text, 'created_question_ids': created_questions, 'created_item_ids': created_items}, message='Marking scheme extracted. Review generated questions and points before publishing.', status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post'], url_path='scripts')
    def scripts(self, request, pk=None):
        exam = self.get_object()

        if request.method == 'GET':
            return api_response(
                data=ExamScriptSerializer(
                    exam.scripts.select_related('candidate', 'student'), many=True, context={'request': request},
                ).data,
            )

        if exam.status != Exam.Status.CLOSED:
            raise ValidationError('Scripts can only be uploaded once the exam is CLOSED.')

        candidate_number = request.data.get('candidate_number')
        if not candidate_number:
            raise ValidationError({'candidate_number': 'This field is required.'})
        try:
            candidate = ExamCandidate.objects.select_related('student').get(
                exam=exam, candidate_number=candidate_number,
            )
        except ExamCandidate.DoesNotExist:
            raise ValidationError({'candidate_number': 'No such candidate registered for this exam.'})

        student = candidate.student
        school = exam.school
        # Defense in depth — Phase 14's ExamCandidate.clean() should
        # already guarantee this, but never trust a chain of assumptions.
        if student.school_id != school.id:
            raise ValidationError({'candidate_number': 'Candidate/student/school mismatch.'})

        uploaded_file = request.FILES.get('file')
        if not uploaded_file:
            raise ValidationError({'file': 'This field is required.'})

        ext = os.path.splitext(uploaded_file.name)[1].lower()
        if ext not in ALLOWED_SCRIPT_EXTENSIONS:
            raise ValidationError({'file': f'File type "{ext or "unknown"}" is not allowed.'})
        if uploaded_file.size > MAX_SCRIPT_SIZE_BYTES:
            raise ValidationError({'file': f'File exceeds the {MAX_SCRIPT_SIZE_BYTES // (1024 * 1024)}MB limit.'})

        checksum = compute_file_checksum(uploaded_file)

        next_version = (
            ExamScript.objects.filter(exam=exam, candidate=candidate).aggregate(Max('version'))['version__max']
            or 0
        ) + 1

        script = ExamScript.objects.create(
            school=school, exam=exam, candidate=candidate, student=student,
            file=uploaded_file, file_name=uploaded_file.name,
            mime_type=mimetypes.guess_type(uploaded_file.name)[0] or 'application/octet-stream',
            file_size=uploaded_file.size, checksum=checksum, version=next_version,
            uploaded_by=request.user,
        )

        attempt, _created = StudentExamAttempt.objects.update_or_create(
            candidate=candidate,
            defaults={'school': school, 'exam': exam, 'student': student, 'current_script': script},
        )
        # A placeholder answer row per question -- scaffolding for Phase
        # 16's marking engine, not populated with content here.
        for exam_question in exam.questions.all():
            StudentExamAnswer.objects.get_or_create(attempt=attempt, exam_question=exam_question)

        return api_response(
            data=ExamScriptSerializer(script, context={'request': request}).data,
            message='Script uploaded', status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['get'], url_path='attempts')
    def attempts(self, request, pk=None):
        exam = self.get_object()
        attempts_qs = exam.attempts.select_related('candidate', 'student', 'current_script').prefetch_related(
            'answers',
        )
        return api_response(data=StudentExamAttemptSerializer(attempts_qs, many=True).data)

    @action(detail=True, methods=['post'], url_path='publish-results')
    def publish_results(self, request, pk=None):
        """Publishes every currently-FINALIZED script for this exam in one
        action (a teacher shouldn't have to publish 40 candidates one at a
        time). Scripts not yet finalized are simply left alone — this is
        NOT an all-or-nothing gate, since candidates' scripts routinely
        finish review at different times. Marks the exam
        RESULTS_PUBLISHED as a one-way signal that publishing has begun
        for it, regardless of how many candidates are covered so far."""
        exam = self.get_object()
        finalized_scripts = list(exam.scripts.filter(status=ExamScript.Status.FINALIZED))

        for script in finalized_scripts:
            script.status = ExamScript.Status.PUBLISHED
            script.save(update_fields=['status', 'updated_at'])

        exam.status = Exam.Status.RESULTS_PUBLISHED
        exam.save(update_fields=['status', 'updated_at'])

        return api_response(
            data={'exam': self.get_serializer(exam).data, 'published_count': len(finalized_scripts)},
            message=f'Published {len(finalized_scripts)} result(s)',
        )


class ExamQuestionViewSet(EnvelopeMixin, TenantScopedQuerysetMixin, viewsets.ModelViewSet):
    """/api/exams/questions/?exam=<id> — authoring-only, mirrors
    quizzes.QuestionViewSet's pattern exactly."""

    queryset = ExamQuestion.objects.select_related('school', 'exam', 'learning_objective').prefetch_related(
        'marking_scheme_items',
    )
    serializer_class = ExamQuestionSerializer
    success_messages = {'create': 'Exam question created', 'update': 'Exam question updated'}

    def get_queryset(self):
        queryset = super().get_queryset()
        exam_id = self.request.query_params.get('exam')
        if exam_id:
            queryset = queryset.filter(exam_id=exam_id)
        user = self.request.user
        if is_system_admin(user) or user.role == 'ADMIN':
            return queryset
        if user.role == 'TEACHER':
            return queryset.filter(exam__created_by=user)
        return queryset.none()

    def get_permissions(self):
        if self.action == 'create':
            return [IsAuthenticated(), (IsAdmin | IsTeacher)()]
        if self.action in ('update', 'partial_update', 'destroy'):
            return [IsAuthenticated(), IsOwnerOrSchoolAdmin()]
        return super().get_permissions()

    def _require_draft_exam(self, exam):
        if exam.status != Exam.Status.DRAFT:
            raise ValidationError('Exam questions can only be modified while the exam is in DRAFT status.')

    def _validate_learning_objective(self, exam, learning_objective):
        if learning_objective and learning_objective.topic.subject_id != exam.subject_id:
            raise ValidationError(
                {'learning_objective': 'This learning objective does not belong to the exam subject.'},
            )

    def perform_create(self, serializer):
        exam = serializer.validated_data.get('exam')
        user = self.request.user
        can_edit = (
            is_system_admin(user)
            or (user.role == 'ADMIN' and user.school_id == exam.school_id)
            or exam.created_by_id == user.id
        )
        if not can_edit:
            raise PermissionDenied("Only this exam's editor may add questions to it.")
        self._require_draft_exam(exam)
        self._validate_learning_objective(exam, serializer.validated_data.get('learning_objective'))
        serializer.save(school=exam.school, created_by=exam.created_by)

    def perform_update(self, serializer):
        instance = serializer.instance
        self._require_draft_exam(instance.exam)
        learning_objective = serializer.validated_data.get('learning_objective', instance.learning_objective)
        self._validate_learning_objective(instance.exam, learning_objective)
        serializer.save()

    def perform_destroy(self, instance):
        self._require_draft_exam(instance.exam)
        instance.delete()

    def _can_edit(self, request, exam_question):
        return IsOwnerOrSchoolAdmin().has_object_permission(request, self, exam_question)

    @action(
        detail=True, methods=['get', 'post', 'delete'], url_path='marking-scheme',
        permission_classes=[IsAuthenticated],
    )
    def marking_scheme(self, request, pk=None):
        exam_question = self.get_object()

        if request.method == 'GET':
            return api_response(
                data=MarkingSchemeItemSerializer(exam_question.marking_scheme_items.all(), many=True).data,
            )

        if not self._can_edit(request, exam_question):
            raise PermissionDenied("Only this exam's editor may manage its marking scheme.")
        self._require_draft_exam(exam_question.exam)

        if request.method == 'DELETE':
            item_id = request.data.get('item_id')
            deleted, _ = MarkingSchemeItem.objects.filter(id=item_id, exam_question=exam_question).delete()
            if not deleted:
                raise ValidationError({'item_id': 'No such marking scheme item on this question.'})
            return api_response(message='Marking scheme item removed')

        serializer = MarkingSchemeItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        marks = serializer.validated_data['marks']

        existing_total = exam_question.marking_scheme_items.aggregate(total=Sum('marks'))['total'] or 0
        if existing_total + marks > exam_question.max_marks:
            raise ValidationError({
                'marks': (
                    f'Marking scheme total ({existing_total + marks}) would exceed '
                    f'max_marks ({exam_question.max_marks}).'
                ),
            })

        item = MarkingSchemeItem.objects.create(exam_question=exam_question, **serializer.validated_data)
        return api_response(
            data=MarkingSchemeItemSerializer(item).data, message='Marking scheme item added',
            status=status.HTTP_201_CREATED,
        )


class StudentExamAnswerViewSet(
    EnvelopeMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet,
):
    """/api/exams/answers/ — marking surface for StudentExamAnswer. An
    OBJECTIVE question is marked deterministically (ExamMarkingService) by
    recording what the student wrote; a SUBJECTIVE one (or an override of
    an objective one) is marked by a teacher setting `teacher_final_marks`
    directly — the manual fallback that always works even before Phase
    17's AI assistance exists. Requires the exam to be in MARKING status.
    """

    queryset = StudentExamAnswer.objects.select_related(
        'attempt__exam', 'attempt__candidate', 'attempt__student', 'exam_question',
    )
    serializer_class = StudentExamAnswerSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        attempt_id = self.request.query_params.get('attempt')
        if attempt_id:
            queryset = queryset.filter(attempt_id=attempt_id)
        user = self.request.user
        if is_system_admin(user):
            return queryset
        if user.role == 'ADMIN':
            return queryset.filter(attempt__school_id=user.school_id)
        if user.role == 'TEACHER':
            return queryset.filter(attempt__exam__created_by=user)
        return queryset.none()

    def _can_mark(self, user, answer):
        exam = answer.attempt.exam
        return (
            is_system_admin(user)
            or (user.role == 'ADMIN' and user.school_id == exam.school_id)
            or exam.created_by_id == user.id
        )

    @action(detail=True, methods=['post'])
    def mark(self, request, pk=None):
        answer = self.get_object()
        if not self._can_mark(request.user, answer):
            raise PermissionDenied("Only this exam's editor may mark its answers.")

        exam = answer.attempt.exam
        if exam.status != Exam.Status.MARKING:
            raise ValidationError('Answers can only be marked while the exam is in MARKING status.')

        question = answer.exam_question
        serializer = MarkAnswerInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if 'recorded_answer' in data:
            answer.recorded_answer = data['recorded_answer']
            answer.save(update_fields=['recorded_answer'])
            if question.question_type == ExamQuestion.Type.OBJECTIVE and 'teacher_final_marks' not in data:
                answer = ExamMarkingService.mark_answer(answer)
                return api_response(data=self.get_serializer(answer).data, message='Answer marked')

        if 'teacher_final_marks' in data:
            marks = data['teacher_final_marks']
            if not (0 <= marks <= question.max_marks):
                raise ValidationError({'teacher_final_marks': f'Must be between 0 and {question.max_marks}.'})
            answer.teacher_final_marks = marks
            if 'teacher_comment' in data:
                answer.teacher_comment = data['teacher_comment']
            answer.marked_at = timezone.now()
            answer.save(update_fields=['teacher_final_marks', 'teacher_comment', 'marked_at'])

        return api_response(data=self.get_serializer(answer).data, message='Answer marked')


class ExamScriptViewSet(EnvelopeMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """/api/exams/scripts/ — drives ExamScript through
    UPLOADED → PROCESSING → MARKED → TEACHER_REVIEW → FINALIZED
    (→ PUBLISHED is Phase 18's job — results publishing, not marking).

    ai-mark calls Qwen (via apps.ai's AIService abstraction, never Ollama
    directly) for every SUBJECTIVE answer that has a recorded_answer
    transcribed. On any invalid/unavailable result the script goes to
    FAILED with `marking_error` set — never partially "MARKED" — and can
    always be retried, or bypassed entirely via manual marking
    (StudentExamAnswerViewSet.mark, Phase 16), which never depends on AI.

    finalize is blocked until EVERY answer has a teacher_final_marks set
    — AI's ai_marks_awarded alone can never finalize anything; a teacher
    must always have taken an explicit action first."""

    queryset = ExamScript.objects.select_related('exam', 'candidate', 'student')
    serializer_class = ExamScriptSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if is_system_admin(user):
            return queryset
        if user.role == 'ADMIN':
            return queryset.filter(school_id=user.school_id)
        if user.role == 'TEACHER':
            return queryset.filter(exam__created_by=user)
        return queryset.none()

    def _can_manage(self, user, script):
        exam = script.exam
        return (
            is_system_admin(user)
            or (user.role == 'ADMIN' and user.school_id == exam.school_id)
            or exam.created_by_id == user.id
        )

    @action(detail=True, methods=['post'], url_path='ocr')
    def ocr(self, request, pk=None):
        script = self.get_object()
        if not self._can_manage(request.user, script):
            raise PermissionDenied("Only this exam's editor may run OCR.")
        script.ocr_status = ExamScript.OCRStatus.PROCESSING
        script.ocr_error = ''
        script.save(update_fields=['ocr_status', 'ocr_error', 'updated_at'])
        try:
            text, method = extract_script(script.file.file)
            mapped = map_answers(text, script.exam.questions.all())
            attempt = script.candidate.attempt
            for answer in attempt.answers.all():
                if answer.exam_question_id in mapped and not answer.recorded_answer:
                    answer.recorded_answer = mapped[answer.exam_question_id]
                    answer.ocr_confidence = 'medium' if method == 'OCR' else 'high'
                    answer.save(update_fields=['recorded_answer', 'ocr_confidence'])
            script.extracted_text = text
            script.ocr_status = ExamScript.OCRStatus.READY
            script.save(update_fields=['extracted_text', 'ocr_status', 'updated_at'])
        except Exception as exc:
            script.ocr_status = ExamScript.OCRStatus.FAILED
            script.ocr_error = str(exc)
            script.save(update_fields=['ocr_status', 'ocr_error', 'updated_at'])
            raise ValidationError({'ocr': str(exc)})
        return api_response(data=self.get_serializer(script).data, message='Text extraction complete')

    @action(detail=True, methods=['post'], url_path='ai-mark')
    def ai_mark(self, request, pk=None):
        script = self.get_object()
        if not self._can_manage(request.user, script):
            raise PermissionDenied("Only this exam's editor may trigger AI marking.")
        if script.exam.status != Exam.Status.MARKING:
            raise ValidationError("AI marking can only run while the exam is in MARKING status.")
        if script.status not in (ExamScript.Status.UPLOADED, ExamScript.Status.FAILED):
            raise ValidationError(f'AI marking cannot start from status {script.status}.')

        attempt = script.candidate.attempt
        subjective_answers = list(
            attempt.answers.select_related('exam_question').filter(
                exam_question__question_type=ExamQuestion.Type.SUBJECTIVE,
            )
        )

        script.status = ExamScript.Status.PROCESSING
        script.marking_error = ''
        script.save(update_fields=['status', 'marking_error', 'updated_at'])

        errors = []
        for answer in subjective_answers:
            if not answer.recorded_answer:
                errors.append(f'Q{answer.exam_question.question_number}: answer not yet transcribed.')
                continue
            try:
                ExamAIMarkingService.mark_answer(answer)
            except AIServiceError as exc:
                errors.append(f'Q{answer.exam_question.question_number}: {exc}')

        if errors:
            script.status = ExamScript.Status.FAILED
            script.marking_error = '; '.join(errors)
            script.save(update_fields=['status', 'marking_error', 'updated_at'])
            return api_response(
                data=self.get_serializer(script).data, message='AI marking failed',
                status=status.HTTP_400_BAD_REQUEST,
            )

        script.status = ExamScript.Status.MARKED
        script.save(update_fields=['status', 'updated_at'])
        return api_response(data=self.get_serializer(script).data, message='AI marking complete')

    @action(detail=True, methods=['post'], url_path='submit-for-review')
    def submit_for_review(self, request, pk=None):
        script = self.get_object()
        if not self._can_manage(request.user, script):
            raise PermissionDenied("Only this exam's editor may submit this script for review.")

        if script.status not in (ExamScript.Status.MARKED, ExamScript.Status.UPLOADED, ExamScript.Status.FAILED):
            raise ValidationError(
                'Only a MARKED script (or one marked entirely manually, without ever using AI) '
                'can be submitted for teacher review.',
            )
        if script.status != ExamScript.Status.MARKED:
            # Skipping ai-mark entirely (never called, or it previously
            # failed) is fine IF every answer already has a
            # teacher_final_marks some other way -- deterministic
            # objective marking, or a teacher marking subjective answers
            # entirely by hand. AI must never be a hard requirement here.
            if script.candidate.attempt.answers.filter(teacher_final_marks__isnull=True).exists():
                raise ValidationError(
                    'This script has not finished marking (AI or manual) for every question yet.',
                )

        script.status = ExamScript.Status.TEACHER_REVIEW
        script.save(update_fields=['status', 'updated_at'])
        return api_response(data=self.get_serializer(script).data, message='Submitted for teacher review')

    @action(detail=True, methods=['post'], url_path='finalize')
    def finalize(self, request, pk=None):
        script = self.get_object()
        if not self._can_manage(request.user, script):
            raise PermissionDenied("Only this exam's editor may finalize this script.")
        if script.status != ExamScript.Status.TEACHER_REVIEW:
            raise ValidationError('Only a script in TEACHER_REVIEW can be finalized.')

        attempt = script.candidate.attempt
        answers = list(attempt.answers.select_related('exam_question'))
        unmarked = [a for a in answers if a.teacher_final_marks is None]
        if unmarked:
            raise ValidationError(
                'Every answer must have a teacher_final_marks before finalizing — '
                f'{len(unmarked)} question(s) still unmarked.',
            )

        script.status = ExamScript.Status.FINALIZED
        script.save(update_fields=['status', 'updated_at'])

        total_marks = sum(a.exam_question.max_marks for a in answers)
        score = sum((a.teacher_final_marks for a in answers), Decimal('0'))
        percentage = (score / total_marks * 100) if total_marks else Decimal('0')

        attempt.total_marks = total_marks
        attempt.score = score.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        attempt.percentage = percentage.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        attempt.save(update_fields=['total_marks', 'score', 'percentage', 'updated_at'])

        return api_response(data=self.get_serializer(script).data, message='Script finalized')

    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        """Only a FINALIZED script can be published — which structurally
        rules out publishing an unreviewed (MARKED/TEACHER_REVIEW) or
        FAILED-AI-marking script, since neither can ever reach FINALIZED
        without first being fixed and explicitly reviewed (Phase 17's
        finalize gate)."""
        script = self.get_object()
        if not self._can_manage(request.user, script):
            raise PermissionDenied("Only this exam's editor may publish this script.")
        if script.status != ExamScript.Status.FINALIZED:
            raise ValidationError('Only a FINALIZED script can be published.')
        script.status = ExamScript.Status.PUBLISHED
        script.save(update_fields=['status', 'updated_at'])
        return api_response(data=self.get_serializer(script).data, message='Script published')


class ExamResultViewSet(EnvelopeMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """/api/exams/results/ — STUDENT-FACING, the first (and only) point in
    the whole exam module where a student gets any read access at all.
    A student sees only their OWN results, and only once the
    corresponding ExamScript has been published — never earlier,
    regardless of how far marking/review has otherwise progressed.
    Exposes total score, percentage, and question-level marks/feedback
    (the teacher's own comment, never a raw unreviewed AI suggestion)."""

    serializer_class = ExamResultSerializer
    permission_classes = [IsAuthenticated, IsStudent]

    def get_queryset(self):
        return StudentExamAttempt.objects.filter(
            student=self.request.user, current_script__status=ExamScript.Status.PUBLISHED,
        ).select_related('exam', 'candidate', 'current_script').prefetch_related('answers__exam_question')
