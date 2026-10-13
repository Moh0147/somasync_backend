import mimetypes
import os
import re
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models import Count, Max, Sum
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from common.mixins import EnvelopeMixin, TenantOwnedViewSetMixin
from common.permissions import (
    IsAdmin, IsOwnerOrSchoolAdmin, IsStudent, IsTeacher, TenantScopedQuerysetMixin,
    MARKING_OPERATIONAL_ROLES, MARKING_READ_ROLES, is_system_admin,
)
from common.utils import api_response, compute_file_checksum

from .ai_marking_service import AIServiceError, ExamAIMarkingService
from .cover_page_service import generate_cover_pages
from .mark_sheet_service import generate_mark_sheet
from .audit import log_marking_action
from .models import (
    ALLOWED_SCRIPT_EXTENSIONS, Exam, ExamCandidate, ExamQuestion, ExamScript,
    ExamScriptAssignment, ExamScriptPage, MarkingAuditLog, MAX_SCRIPT_SIZE_BYTES,
    MarkingSchemeDocument, MarkingSchemeItem, StudentExamAnswer,
    StudentExamAttempt,
)
from .services import ExamMarkingService
from .ocr_service import extract_pages_as_png, extract_script, map_answers, vision_extract_answers
from .serializers import (
    ExamCandidateInputSerializer, ExamCandidateSerializer, ExamQuestionSerializer, ExamResultSerializer,
    ExamScriptAssignmentSerializer, ExamScriptSerializer, ExamSerializer, MarkAnswerInputSerializer,
    MarkingAuditLogSerializer, MarkingSchemeItemSerializer,
    StudentCandidateNumberSerializer, StudentExamAnswerSerializer, StudentExamAttemptSerializer,
)

User = get_user_model()

class ExamViewSet(EnvelopeMixin, TenantOwnedViewSetMixin, viewsets.ModelViewSet):

    queryset = Exam.objects.select_related('school', 'subject', 'class_level', 'created_by')
    serializer_class = ExamSerializer
    success_messages = {'create': 'Exam created', 'update': 'Exam updated'}

    def get_queryset(self):
        user = self.request.user
        if user.role == 'EXAM_MARKING_MANAGER' and not user.school_id:
            return Exam.objects.select_related('school', 'subject', 'class_level', 'created_by')
        queryset = super().get_queryset()
        if is_system_admin(user) or user.role == 'ADMIN':
            return queryset
        if user.role == 'TEACHER':
            return queryset.filter(created_by=user)
        if user.role in MARKING_READ_ROLES:
            qs = queryset.filter(school=user.school)
            if user.role == 'EXAMINER':
                qs = qs.filter(status__in=[
                    Exam.Status.MARKING, Exam.Status.REVIEW,
                    Exam.Status.FINALIZED, Exam.Status.RESULTS_PUBLISHED,
                ])
            return qs
        return queryset.none()

    def get_permissions(self):
        if self.action == 'create':
            return [IsAuthenticated(), (IsAdmin | IsTeacher)()]
        if self.action == 'scripts':
            return [IsAuthenticated()]
        if self.action == 'start_marking':
            return [IsAuthenticated()]
        if self.action in (
            'update', 'partial_update', 'destroy', 'publish', 'close', 'candidates', 'attempts',
            'publish_results', 'bulk_publish_results', 'upload_exam_paper', 'upload_marking_scheme',
            'mark_sheet', 'cover_pages',
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

    @action(detail=True, methods=['patch'], url_path='section-settings',
            permission_classes=[IsAuthenticated, IsOwnerOrSchoolAdmin])
    def set_section_settings(self, request, pk=None):
        """Update section_settings without requiring DRAFT status — teachers may
        need to define choice sections after the exam has already been published."""
        exam = self.get_object()
        settings_data = request.data.get('section_settings')
        if not isinstance(settings_data, dict):
            raise ValidationError({'section_settings': 'Must be a JSON object mapping section name to required answer count.'})
        for key, value in settings_data.items():
            if not isinstance(key, str) or not isinstance(value, int) or value < 1:
                raise ValidationError({'section_settings': f'Each value must be a positive integer (got {key!r}: {value!r}).'})
        exam.section_settings = settings_data
        exam.save(update_fields=['section_settings', 'updated_at'])
        return api_response(data=self.get_serializer(exam).data, message='Section settings updated')

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

    @action(detail=True, methods=['get'], url_path='mark-sheet')
    def mark_sheet(self, request, pk=None):
        """Return a printable PDF mark sheet with one row per candidate.

        Columns: candidate number, name, marks per question (Q1…Qn), total, %.
        Available from FINALIZED status onwards so all marks are locked in.
        Also available in MARKING / REVIEW for a partial preview.
        """
        exam = self.get_object()
        allowed = (
            Exam.Status.MARKING, Exam.Status.REVIEW,
            Exam.Status.FINALIZED, Exam.Status.RESULTS_PUBLISHED,
        )
        if exam.status not in allowed:
            raise ValidationError('Mark sheet is only available once marking has started.')

        questions = list(
            exam.questions.prefetch_related('marking_scheme_items').order_by('order', 'question_number')
        )
        candidates = list(
            exam.candidates.select_related('student', 'attempt').order_by('candidate_number')
        )

        rows = []
        for cand in candidates:
            attempt = getattr(cand, 'attempt', None)
            marks_map = {}
            if attempt is not None:
                for ans in attempt.answers.select_related('exam_question').all():
                    marks_map[ans.exam_question_id] = ans.teacher_final_marks

            rows.append({
                'candidate_number': cand.candidate_number,
                'student_name': cand.student.get_full_name() or cand.student.username,
                'marks': marks_map,
                'total': attempt.score if attempt else None,
                'percentage': attempt.percentage if attempt else None,
            })

        pdf_bytes = generate_mark_sheet(exam, questions, rows)
        safe_title = re.sub(r'[^\w\s-]', '', exam.title).strip().replace(' ', '_')
        filename = f'{safe_title}_mark_sheet.pdf'
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    @action(detail=True, methods=['get'], url_path='cover-pages')
    def cover_pages(self, request, pk=None):
        """Return a printable PDF with one cover page per registered candidate.

        Each cover page has the candidate number (large) and a QR code
        encoding the candidate_number, so the camera scanner can auto-detect
        the candidate when scanning physical scripts after the exam.
        """
        exam = self.get_object()
        candidates = exam.candidates.select_related('student').order_by('candidate_number')
        if not candidates.exists():
            return api_response(status=400, message='No candidates registered for this exam.')
        pdf_bytes = generate_cover_pages(exam, candidates)
        safe_title = re.sub(r'[^\w\s-]', '', exam.title).strip().replace(' ', '_')[:40]
        filename = f'{safe_title}_cover_pages.pdf'
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

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
        user = request.user
        can_start = (
            is_system_admin(user)
            or (user.role == 'ADMIN' and user.school_id == exam.school_id)
            or exam.created_by_id == user.id
            or (
                user.role == 'EXAM_MARKING_MANAGER'
                and (not user.school_id or user.school_id == exam.school_id)
            )
        )
        if not can_start:
            raise PermissionDenied('Only this exam\'s owner, a school admin, or the Marking Manager may start marking.')
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

    @action(detail=True, methods=['post'], url_path='bulk-candidates')
    def bulk_candidates(self, request, pk=None):
        """Enroll multiple students at once with pre-generated candidate numbers."""
        exam = self.get_object()
        entries = request.data.get('candidates', [])
        if not isinstance(entries, list) or not entries:
            raise ValidationError({'candidates': 'Provide a non-empty list of {student, candidate_number} objects.'})

        enrolled = []
        skipped = []
        errors = []
        existing_numbers = set(exam.candidates.values_list('candidate_number', flat=True))
        existing_students = set(exam.candidates.values_list('student_id', flat=True))

        for entry in entries:
            student_id = entry.get('student')
            candidate_number = str(entry.get('candidate_number', '')).strip()

            if not student_id or not candidate_number:
                errors.append(f'Invalid entry: {entry}')
                continue

            if student_id in existing_students:
                skipped.append(candidate_number)
                continue
            if candidate_number in existing_numbers:
                errors.append(f'Namba {candidate_number} imetumika tayari.')
                continue

            try:
                student = User.objects.get(pk=student_id, role=User.Role.STUDENT, school_id=exam.school_id)
            except User.DoesNotExist:
                errors.append(f'Mwanafunzi {student_id} hapatikani.')
                continue

            ExamCandidate.objects.create(exam=exam, student=student, candidate_number=candidate_number)
            enrolled.append(candidate_number)
            existing_numbers.add(candidate_number)
            existing_students.add(student_id)

        return api_response(
            data={'enrolled': enrolled, 'skipped': skipped, 'errors': errors},
            status=status.HTTP_201_CREATED if enrolled else status.HTTP_200_OK,
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
                question_type=ExamQuestion.Type.SUBJECTIVE, max_marks=Decimal(match.group(2) or '1'),
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
            max_marks = Decimal(match.group(2))
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
            scripts_qs = exam.scripts.select_related('candidate', 'student')
            return api_response(
                data=ExamScriptSerializer(scripts_qs, many=True, context={'request': request}).data,
            )

        user = request.user
        _is_examiner = user.role == 'EXAMINER'
        can_upload = (
            is_system_admin(user)
            or (user.role == 'ADMIN' and user.school_id == exam.school_id)
            or exam.created_by_id == user.id
            or (
                user.role == 'EXAM_MARKING_MANAGER'
                and (not user.school_id or user.school_id == exam.school_id)
            )
            or (_is_examiner and user.school_id == exam.school_id)
        )
        if not can_upload:
            raise PermissionDenied('Only this exam\'s owner, school admin, Marking Manager, or an Examiner at this school may upload scripts.')

        if exam.status not in (Exam.Status.CLOSED, Exam.Status.MARKING):
            raise ValidationError('Scripts can only be uploaded once the exam is CLOSED or in MARKING.')

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
        for exam_question in exam.questions.all():
            StudentExamAnswer.objects.get_or_create(attempt=attempt, exam_question=exam_question)

        if _is_examiner:
            ExamScriptAssignment.objects.get_or_create(
                script=script,
                assignee=request.user,
                role=ExamScriptAssignment.Role.EXAMINER,
                defaults={'assigned_by': request.user},
            )

        _extract_and_save_pages(script)

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

    @action(detail=False, methods=['post'], url_path='bulk-publish-results')
    def bulk_publish_results(self, request):
        """Publish results for multiple exams in one action.
        Body: { "exam_ids": [1, 2, 3] }
        Only publishes exams owned by this teacher (or visible to school admin).
        Each exam must have at least one FINALIZED script."""
        exam_ids = request.data.get('exam_ids', [])
        if not isinstance(exam_ids, list) or not exam_ids:
            raise ValidationError({'exam_ids': 'Provide a non-empty list of exam IDs.'})

        accessible = {e.pk: e for e in self.get_queryset().filter(pk__in=exam_ids)}
        total_published = 0
        published_exams = []

        for eid in exam_ids:
            exam = accessible.get(eid)
            if not exam:
                continue
            finalized_scripts = list(exam.scripts.filter(status=ExamScript.Status.FINALIZED))
            for script in finalized_scripts:
                script.status = ExamScript.Status.PUBLISHED
                script.save(update_fields=['status', 'updated_at'])
            total_published += len(finalized_scripts)
            exam.status = Exam.Status.RESULTS_PUBLISHED
            exam.save(update_fields=['status', 'updated_at'])
            published_exams.append({'exam_id': exam.pk, 'title': exam.title, 'published_count': len(finalized_scripts)})

        return api_response(
            data={'published_exams': published_exams, 'total_published': total_published},
            message=f'Published results for {len(published_exams)} exam(s), {total_published} script(s) total.',
        )

    @action(detail=False, methods=['get'], url_path='marking-summary')
    def marking_summary(self, request):
        """GET /api/exams/marking-summary/ — per-exam script status counts.

        Intended for EXAM_MARKING_MANAGER dashboards. Returns all exams visible
        to the calling user that are in an active marking lifecycle phase, with
        a count breakdown of script statuses. A single aggregation query is used
        to avoid N+1 loading."""
        active_statuses = {
            Exam.Status.MARKING, Exam.Status.REVIEW,
            Exam.Status.FINALIZED, Exam.Status.RESULTS_PUBLISHED,
        }
        exams = (
            self.get_queryset()
            .filter(status__in=active_statuses)
            .prefetch_related('subject', 'scripts')
            .order_by('-updated_at')
        )

        from django.db.models import Q
        script_counts = (
            ExamScript.objects
            .filter(exam__in=exams)
            .values('exam_id', 'status')
            .annotate(n=Count('id'))
        )
        counts_by_exam: dict = {}
        for row in script_counts:
            counts_by_exam.setdefault(row['exam_id'], {})[row['status']] = row['n']

        all_statuses = ['UPLOADED', 'PROCESSING', 'MARKED', 'TEACHER_REVIEW', 'FINALIZED', 'PUBLISHED', 'FAILED']
        result = []
        for exam in exams:
            exam_counts = counts_by_exam.get(exam.pk, {})
            result.append({
                'exam_id': exam.pk,
                'title': exam.title,
                'subject_name': exam.subject.name if exam.subject_id else '',
                'academic_year': exam.academic_year,
                'exam_status': exam.status,
                'total_scripts': sum(exam_counts.values()),
                'counts': {s: exam_counts.get(s, 0) for s in all_statuses},
            })
        return api_response(data=result)

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

    def _require_editable_exam(self, exam):
        """Wider gate used for marking scheme items — allows editing in MARKING
        status too (NECTA examiners enter the answer key after the exam is set up)."""
        if exam.status not in (Exam.Status.DRAFT, Exam.Status.MARKING):
            raise ValidationError('Marking scheme items can only be edited in DRAFT or MARKING status.')

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

    @action(detail=True, methods=['patch'], url_path='compulsory', permission_classes=[IsAuthenticated])
    def set_compulsory(self, request, pk=None):
        """Toggle is_compulsory on a question. Not restricted to DRAFT — teachers
        may need to mark Section C questions as optional after the exam is published."""
        question = self.get_object()
        if not self._can_edit(request, question):
            raise PermissionDenied("Only this exam's editor may change question settings.")
        value = request.data.get('is_compulsory')
        if not isinstance(value, bool):
            raise ValidationError({'is_compulsory': 'Must be a boolean.'})
        question.is_compulsory = value
        question.save(update_fields=['is_compulsory', 'updated_at'])
        return api_response(data=ExamQuestionSerializer(question).data, message='Question updated')

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
        self._require_editable_exam(exam_question.exam)

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
        if user.role in MARKING_READ_ROLES:
            if user.role == 'EXAM_MARKING_MANAGER' and not user.school_id:
                qs = queryset
            else:
                qs = queryset.filter(attempt__school_id=user.school_id)
            return qs
        return queryset.none()

    def _can_mark(self, user, answer):
        exam = answer.attempt.exam
        if is_system_admin(user):
            return False
        if user.role == 'ADMIN' and user.school_id == exam.school_id:
            return True
        if user.role == 'TEACHER' and exam.created_by_id == user.id:
            return True
        if user.role == 'EXAM_MARKING_MANAGER':
            return not user.school_id or user.school_id == exam.school_id
        if user.role == 'EXAMINER' and user.school_id == exam.school_id:
            return exam.status in ('MARKING', 'REVIEW', 'FINALIZED', 'RESULTS_PUBLISHED')
        return False

    @action(detail=True, methods=['post'])
    def mark(self, request, pk=None):
        answer = self.get_object()
        if not self._can_mark(request.user, answer):
            raise PermissionDenied("Only this exam's editor may mark its answers.")

        exam = answer.attempt.exam
        if exam.status not in (Exam.Status.MARKING, Exam.Status.RESULTS_PUBLISHED):
            raise ValidationError('Answers can only be marked while the exam is in MARKING or RESULTS_PUBLISHED status.')

        question = answer.exam_question
        serializer = MarkAnswerInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if 'recorded_answer' in data:
            answer.recorded_answer = data['recorded_answer']
            answer.save(update_fields=['recorded_answer'])
            if question.question_type == ExamQuestion.Type.OBJECTIVE and 'teacher_final_marks' not in data:
                answer = ExamMarkingService.mark_answer(answer)
                log_marking_action(
                    request, MarkingAuditLog.Action.ANSWER_MARKED,
                    script=answer.attempt.current_script,
                    detail={'question': question.question_number,
                            'marks': str(answer.teacher_final_marks), 'method': 'objective'},
                )
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

        log_marking_action(
            request, MarkingAuditLog.Action.ANSWER_MARKED,
            script=answer.attempt.current_script,
            detail={'question': question.question_number,
                    'marks': str(answer.teacher_final_marks), 'method': 'manual'},
        )
        return api_response(data=self.get_serializer(answer).data, message='Answer marked')

def _extract_and_save_pages(script: ExamScript) -> None:
    """Render each page of `script.file` as a PNG and store as ExamScriptPage rows.

    Best-effort: if extraction fails for any reason the upload itself is not
    rolled back — the teacher still has the original file and can run OCR.
    Existing pages for this script version are deleted first so re-runs are
    idempotent."""
    try:
        script.file.seek(0)
        png_list = extract_pages_as_png(script.file)
    except Exception:
        return

    ExamScriptPage.objects.filter(script=script).delete()
    candidate_num = script.candidate.candidate_number.replace('/', '_')
    for i, png_bytes in enumerate(png_list, start=1):
        filename = f'{candidate_num}_v{script.version}_p{i}.png'
        page = ExamScriptPage(script=script, page_number=i)
        page.image.save(filename, ContentFile(png_bytes), save=True)

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
    pagination_class = None

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if is_system_admin(user):
            return queryset
        if user.role == 'ADMIN':
            return queryset.filter(school_id=user.school_id)
        if user.role == 'TEACHER':
            return queryset.filter(exam__created_by=user)
        if user.role in MARKING_READ_ROLES:
            if user.role == 'EXAM_MARKING_MANAGER' and not user.school_id:
                qs = queryset
            else:
                qs = queryset.filter(school_id=user.school_id)
            exam_id = self.request.query_params.get('exam')
            status_filter = self.request.query_params.get('status')
            if exam_id:
                qs = qs.filter(exam_id=exam_id)
            if status_filter:
                qs = qs.filter(status=status_filter)
            return qs
        return queryset.none()

    def _can_manage(self, user, script):
        exam = script.exam
        if is_system_admin(user):
            return False
        if user.role == 'ADMIN' and user.admin_scope == 'SCHOOL' and user.school_id == exam.school_id:
            return True
        if user.role == 'TEACHER' and exam.created_by_id == user.id:
            return True
        if user.role == 'EXAM_MARKING_MANAGER':
            return not user.school_id or user.school_id == exam.school_id
        if user.role == 'EXAMINER':
            return (
                user.school_id == exam.school_id
                and exam.status in (
                    'MARKING', 'REVIEW', 'FINALIZED', 'RESULTS_PUBLISHED',
                )
            )
        return False

    @action(detail=True, methods=['post'], url_path='ocr')
    def ocr(self, request, pk=None):
        script = self.get_object()
        if not self._can_manage(request.user, script):
            raise PermissionDenied("Only this exam's editor may run OCR.")

        if not script.pages.exists():
            _extract_and_save_pages(script)

        script.ocr_status = ExamScript.OCRStatus.PROCESSING
        script.ocr_error = ''
        script.save(update_fields=['ocr_status', 'ocr_error', 'updated_at'])

        questions = script.exam.questions.all()

        mapped: dict = {}
        text = ''
        method = 'OCR'
        try:
            mapped = vision_extract_answers(script.file.file, questions)
        except Exception:
            pass

        unmapped_questions = [q for q in questions if q.id not in mapped]
        if unmapped_questions:
            try:
                text, method = extract_script(script.file.file)
            except Exception as exc:
                if not mapped:
                    script.ocr_status = ExamScript.OCRStatus.FAILED
                    script.ocr_error = str(exc)
                    script.save(update_fields=['ocr_status', 'ocr_error', 'updated_at'])
                    raise ValidationError({'ocr': str(exc)})
            if text:
                try:
                    fallback = map_answers(text, unmapped_questions)
                    mapped.update(fallback)
                except Exception:
                    pass

        mapping_warning = ''
        try:
            attempt = script.candidate.attempt
            for answer in attempt.answers.all():
                if answer.exam_question_id in mapped and not answer.recorded_answer:
                    answer.recorded_answer = mapped[answer.exam_question_id]
                    answer.ocr_confidence = 'medium' if method == 'OCR' else 'high'
                    answer.save(update_fields=['recorded_answer', 'ocr_confidence'])
        except Exception as exc:
            mapping_warning = str(exc)

        script.extracted_text = text
        script.ocr_status = ExamScript.OCRStatus.READY
        script.ocr_error = f'Answer mapping partial: {mapping_warning}' if mapping_warning else ''
        if script.status == ExamScript.Status.FAILED:
            script.status = ExamScript.Status.UPLOADED
        script.save(update_fields=['extracted_text', 'ocr_status', 'ocr_error', 'status', 'updated_at'])
        log_marking_action(request, MarkingAuditLog.Action.SCRIPT_OCR_RAN, script=script)
        return api_response(data=self.get_serializer(script).data, message='Text extraction complete')

    @action(detail=True, methods=['post'], url_path='ai-mark')
    def ai_mark(self, request, pk=None):
        script = self.get_object()
        if not self._can_manage(request.user, script):
            raise PermissionDenied("Only this exam's editor may trigger AI marking.")
        if script.exam.status not in (Exam.Status.CLOSED, Exam.Status.MARKING, Exam.Status.RESULTS_PUBLISHED):
            raise ValidationError("AI marking can only run while the exam is CLOSED, in MARKING, or RESULTS_PUBLISHED status.")
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
                data=self.get_serializer(script).data, message='AI marking completed with errors',
            )

        script.status = ExamScript.Status.MARKED
        script.marking_error = ''
        script.save(update_fields=['status', 'marking_error', 'updated_at'])
        log_marking_action(request, MarkingAuditLog.Action.SCRIPT_AI_MARKED, script=script)
        return api_response(data=self.get_serializer(script).data, message='AI marking complete')

    @action(detail=True, methods=['get'], url_path='answers')
    def answers(self, request, pk=None):
        """GET /api/exams/scripts/:id/answers/ — returns the answers for the
        script's candidate attempt, enriched with question detail fields so
        the marking UI can render question text + max_marks alongside each answer."""
        script = self.get_object()
        try:
            attempt = script.candidate.attempt
        except Exception:
            return api_response(data=[])
        answers_qs = attempt.answers.select_related('exam_question').order_by('exam_question__order')
        return api_response(data=StudentExamAnswerSerializer(answers_qs, many=True).data)

    @action(detail=True, methods=['post'], url_path='submit-for-review')
    def submit_for_review(self, request, pk=None):
        script = self.get_object()
        if not self._can_manage(request.user, script):
            raise PermissionDenied("Only this exam's editor may submit this script for review.")

        if script.status not in (ExamScript.Status.MARKED, ExamScript.Status.UPLOADED, ExamScript.Status.FAILED):
            raise ValidationError(
                'Only a MARKED, UPLOADED, or FAILED script can be submitted for teacher review.',
            )

        if script.status == ExamScript.Status.UPLOADED:
            unmarked_count = script.candidate.attempt.answers.filter(teacher_final_marks__isnull=True).count()
            if unmarked_count:
                raise ValidationError(
                    f'{unmarked_count} answer(s) still have no confirmed mark. '
                    'Mark all answers before submitting for review.'
                )

        script.status = ExamScript.Status.TEACHER_REVIEW
        script.save(update_fields=['status', 'updated_at'])
        log_marking_action(
            request, MarkingAuditLog.Action.SCRIPT_SUBMITTED, script=script,
            detail={'from_status': script.status},
        )
        return api_response(data=self.get_serializer(script).data, message='Submitted for teacher review')

    @action(detail=True, methods=['post'], url_path='finalize')
    def finalize(self, request, pk=None):
        script = self.get_object()
        user = request.user
        _CAN_FINALIZE = (
            user.role == 'EXAM_MARKING_MANAGER'
            or (user.role == 'ADMIN' and user.admin_scope == 'SCHOOL')
            or (user.role == 'TEACHER' and script.exam.created_by_id == user.id)
        )
        if not _CAN_FINALIZE:
            raise PermissionDenied('Only this exam\'s teacher or marking manager may finalize scripts.')
        if not self._can_manage(user, script):
            raise PermissionDenied("Only this exam's editor may finalize this script.")
        _teacher_direct = user.role == 'TEACHER' and script.exam.created_by_id == user.id
        _allowed_statuses = (
            (ExamScript.Status.TEACHER_REVIEW, ExamScript.Status.MARKED, ExamScript.Status.UPLOADED, ExamScript.Status.FAILED)
            if _teacher_direct
            else (ExamScript.Status.TEACHER_REVIEW,)
        )
        if script.status not in _allowed_statuses:
            raise ValidationError(
                'Script must be in TEACHER_REVIEW before finalizing.'
                if not _teacher_direct
                else 'Script must be UPLOADED, MARKED, or in TEACHER_REVIEW before finalizing.'
            )

        attempt = script.candidate.attempt
        answers = list(attempt.answers.select_related('exam_question'))
        section_rules = script.exam.section_settings or {}

        auto_zeroed_ids = set()
        for a in answers:
            q = a.exam_question
            in_choice_section = bool(q.section and q.section in section_rules)
            if (
                a.teacher_final_marks is None
                and (in_choice_section or not q.is_compulsory)
                and not (a.recorded_answer or '').strip()
            ):
                a.teacher_final_marks = Decimal('0')
                a.teacher_comment = 'Not attempted'
                a.save(update_fields=['teacher_final_marks', 'teacher_comment'])
                auto_zeroed_ids.add(a.id)

        unmarked = [a for a in answers if a.teacher_final_marks is None]
        if unmarked:
            raise ValidationError(
                'Every answer must have a teacher_final_marks before finalizing — '
                f'{len(unmarked)} question(s) still unmarked.',
            )

        script.status = ExamScript.Status.FINALIZED
        script.save(update_fields=['status', 'updated_at'])

        total_marks = sum(a.exam_question.max_marks for a in answers if a.id not in auto_zeroed_ids)
        score = sum((a.teacher_final_marks for a in answers), Decimal('0'))
        percentage = (score / total_marks * 100) if total_marks else Decimal('0')

        attempt.total_marks = int(total_marks)
        attempt.score = score.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        attempt.percentage = percentage.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        attempt.save(update_fields=['total_marks', 'score', 'percentage', 'updated_at'])

        log_marking_action(
            request, MarkingAuditLog.Action.SCRIPT_FINALIZED, script=script,
            detail={'score': str(attempt.score), 'total_marks': attempt.total_marks,
                    'percentage': str(attempt.percentage)},
        )
        return api_response(data=self.get_serializer(script).data, message='Script finalized')

    @action(detail=True, methods=['get', 'post', 'delete'], url_path='assign')
    def assign(self, request, pk=None):
        """GET: list current assignments for this script.
        POST: assign an EXAMINER — body: {assignee_id, role}.
        DELETE: remove an assignment — body: {assignee_id, role}.
        Only EXAM_MARKING_MANAGER and school ADMIN may assign."""
        script = self.get_object()
        user = request.user

        if request.method == 'GET':
            assignments = script.assignments.select_related('assignee', 'assigned_by')
            return api_response(data=ExamScriptAssignmentSerializer(assignments, many=True).data)

        _CAN_ASSIGN = {'EXAM_MARKING_MANAGER', 'ADMIN'}
        if user.role not in _CAN_ASSIGN and not is_system_admin(user):
            raise PermissionDenied('Only EXAM_MARKING_MANAGER may assign scripts.')
        if user.role == 'ADMIN' and user.school_id != script.school_id:
            raise PermissionDenied('School admin may only assign scripts within their school.')

        assignee_id = request.data.get('assignee_id')
        role = request.data.get('role')
        if not assignee_id or role != 'EXAMINER':
            raise ValidationError({'detail': 'assignee_id and role (EXAMINER) are required.'})

        try:
            assignee = User.objects.get(pk=assignee_id, school_id=script.school_id)
        except User.DoesNotExist:
            raise ValidationError({'assignee_id': 'No such user at this school.'})
        _is_examiner = assignee.role == 'EXAMINER' or (
            assignee.role == 'TEACHER' and assignee.secondary_role == 'EXAMINER'
        )
        if not _is_examiner:
            raise ValidationError({'assignee_id': 'Assignee must be an Examiner or a Teacher with Examiner secondary role.'})

        if request.method == 'DELETE':
            deleted, _ = ExamScriptAssignment.objects.filter(
                script=script, assignee=assignee, role=role,
            ).delete()
            if not deleted:
                raise ValidationError({'detail': 'No such assignment exists.'})
            log_marking_action(
                request, MarkingAuditLog.Action.SCRIPT_UNASSIGNED, script=script,
                detail={'assignee': assignee.username, 'role': role},
            )
            return api_response(message='Assignment removed.')

        assignment, created = ExamScriptAssignment.objects.get_or_create(
            script=script, assignee=assignee, role=role,
            defaults={'assigned_by': user},
        )
        log_marking_action(
            request, MarkingAuditLog.Action.SCRIPT_ASSIGNED, script=script,
            detail={'assignee': assignee.username, 'role': role},
        )
        return api_response(
            data=ExamScriptAssignmentSerializer(assignment).data,
            message='Assignment created.' if created else 'Assignment already exists.',
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=['get'], url_path='assignable-users')
    def assignable_users(self, request, pk=None):
        """Return users at this script's school who can be assigned as examiners:
        pure EXAMINER accounts and TEACHERs with secondary_role='EXAMINER'.
        Only EXAM_MARKING_MANAGER and school ADMIN may call this."""
        script = self.get_object()
        user = request.user
        _CAN_ASSIGN = {'EXAM_MARKING_MANAGER', 'ADMIN'}
        if user.role not in _CAN_ASSIGN and not is_system_admin(user):
            raise PermissionDenied('Only EXAM_MARKING_MANAGER may view assignable users.')
        if user.role == 'ADMIN' and user.school_id != script.school_id:
            raise PermissionDenied('School admin may only view users within their school.')

        from django.db.models import Q
        users = (
            User.objects.filter(school_id=script.school_id)
            .filter(
                Q(role='EXAMINER') | Q(role='TEACHER', secondary_role='EXAMINER')
            )
            .order_by('first_name', 'last_name', 'username')
            .values('id', 'username', 'first_name', 'last_name', 'role', 'secondary_role')
        )
        data = [
            {
                'id': u['id'],
                'username': u['username'],
                'full_name': f"{u['first_name']} {u['last_name']}".strip() or u['username'],
                'role': u['role'],
                'secondary_role': u['secondary_role'],
            }
            for u in users
        ]
        return api_response(data=data)

    @action(detail=True, methods=['post'], url_path='reset')
    def reset(self, request, pk=None):
        """Emergency: reset a FAILED or PROCESSING script back to UPLOADED.
        Only EXAM_MARKING_MANAGER may invoke this."""
        script = self.get_object()
        user = request.user
        _CAN_RESET = {'EXAM_MARKING_MANAGER'}
        if user.role not in _CAN_RESET:
            raise PermissionDenied('Only EXAM_MARKING_MANAGER may perform an emergency script reset.')
        if user.school_id != script.school_id:
            raise PermissionDenied('Cannot reset scripts outside your school.')
        if script.status not in (ExamScript.Status.FAILED, ExamScript.Status.PROCESSING):
            raise ValidationError(
                f'Only FAILED or PROCESSING scripts can be reset (current: {script.status}).',
            )
        old_status = script.status
        script.status = ExamScript.Status.UPLOADED
        script.marking_error = ''
        script.save(update_fields=['status', 'marking_error', 'updated_at'])
        log_marking_action(
            request, MarkingAuditLog.Action.SCRIPT_RESET, script=script,
            detail={'from_status': old_status},
        )
        return api_response(data=self.get_serializer(script).data, message='Script reset to UPLOADED.')

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

    @action(detail=False, methods=['get'], url_path='candidate-numbers')
    def candidate_numbers(self, request):
        candidates = ExamCandidate.objects.filter(student=request.user).select_related('exam').order_by(
            '-created_at', 'exam__title',
        )
        return api_response(data=StudentCandidateNumberSerializer(candidates, many=True).data)

    def get_queryset(self):
        return StudentExamAttempt.objects.filter(
            student=self.request.user, current_script__status=ExamScript.Status.PUBLISHED,
        ).select_related('exam', 'candidate', 'current_script').prefetch_related('answers__exam_question')

class AuditLogViewSet(
    EnvelopeMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet,
):
    """/api/exams/audit/ — marking audit trail. Read-only. Restricted to
    EXAM_MARKING_MANAGER (operational) and SYSTEM_ADMIN (governance)."""

    serializer_class = MarkingAuditLogSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        queryset = MarkingAuditLog.objects.select_related('exam', 'script__candidate', 'actor')
        if is_system_admin(user):
            pass
        elif user.role == 'EXAM_MARKING_MANAGER':
            queryset = queryset.filter(exam__school_id=user.school_id)
        else:
            return queryset.none()

        exam_id = self.request.query_params.get('exam')
        action_filter = self.request.query_params.get('action')
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        actor_username = self.request.query_params.get('actor')

        if exam_id:
            queryset = queryset.filter(exam_id=exam_id)
        if action_filter:
            queryset = queryset.filter(action=action_filter)
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        if actor_username:
            queryset = queryset.filter(actor_username__icontains=actor_username)

        return queryset

