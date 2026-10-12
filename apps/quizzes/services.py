from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Question, QuestionOption, Quiz, StudentQuizAnswer, StudentQuizAttempt

class QuizMarkingService:
    """Deterministic marking for MCQ / TRUE_FALSE / MATCHING questions —
    the ONLY marking path for normal quizzes. Never calls AI: every result
    is a pure function of the stored answer key (QuestionOption.is_correct,
    MatchingPair.right_text) and the student's recorded answers, so the
    same attempt always marks the same way.

    AI is reserved for "Explain My Mistake" and AI-assisted marking of
    subjective SCHOOL EXAM answers (Phase 13/17) — never for this.
    """

    @classmethod
    def mark_attempt(cls, attempt):
        """Marks every question in `attempt`'s quiz, updates each
        StudentQuizAnswer's marks_awarded/is_correct, and sets the
        attempt's score/total_marks/percentage. Safe to call more than
        once on the same attempt — same inputs always produce the same
        result (an unanswered question simply scores 0, never an error)."""

        answers_by_question = {a.question_id: a for a in attempt.answers.select_related('selected_option')}
        total_marks = 0
        score = Decimal('0')

        for question in attempt.quiz.questions.prefetch_related('options', 'pairs'):
            total_marks += question.marks
            answer = answers_by_question.get(question.id)
            marks_awarded, is_correct = cls._mark_question(question, answer)

            if answer is not None:
                answer.marks_awarded = marks_awarded
                answer.is_correct = is_correct
                answer.save(update_fields=['marks_awarded', 'is_correct'])

            score += marks_awarded

        percentage = (score / total_marks * 100) if total_marks else Decimal('0')

        attempt.score = score.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        attempt.total_marks = total_marks
        attempt.percentage = percentage.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        attempt.save(update_fields=['score', 'total_marks', 'percentage', 'updated_at'])

        if hasattr(attempt, '_prefetched_objects_cache'):
            attempt._prefetched_objects_cache.pop('answers', None)

        return attempt

    @classmethod
    def _mark_question(cls, question, answer):
        if answer is None:
            return Decimal('0'), False

        if question.question_type in (Question.Type.MCQ, Question.Type.TRUE_FALSE):
            return cls._mark_choice_question(question, answer)
        if question.question_type == Question.Type.MATCHING:
            return cls._mark_matching_question(question, answer)

        return Decimal('0'), False

    @classmethod
    def _mark_choice_question(cls, question, answer):
        option = answer.selected_option
        if option is None or option.question_id != question.id:
            return Decimal('0'), False
        if option.is_correct:
            return Decimal(question.marks), True
        return Decimal('0'), False

    @classmethod
    def _mark_matching_question(cls, question, answer):
        pairs = list(question.pairs.all())
        if not pairs:
            return Decimal('0'), False

        response = answer.matching_response or {}
        correct_count = sum(1 for pair in pairs if response.get(str(pair.id)) == pair.right_text)

        is_correct = correct_count == len(pairs)
        marks_awarded = Decimal(question.marks) * correct_count / len(pairs)
        return marks_awarded, is_correct

def quiz_assigned_to_student(quiz, student):
    from apps.classes.models import StudentClassMembership
    class_ids = StudentClassMembership.objects.filter(student=student).values_list('school_class_id', flat=True)
    return quiz.assignments.filter(school_class_id__in=class_ids).exists()

def validate_quiz_for_attempt(quiz_id, user):
    """Shared by the live `start`/`sync` actions (Phase 8) and the generic
    sync engine (Phase 11) — single source of truth for "can this student
    attempt this quiz." Raises ValidationError; there's no row to attach
    an error to at this point, so a plain 400 is correct here."""
    try:
        quiz = Quiz.objects.get(pk=quiz_id)
    except Quiz.DoesNotExist:
        raise ValidationError({'quiz': 'No such quiz.'})
    if quiz.school_id != user.school_id:
        raise ValidationError({'quiz': 'No such quiz.'})
    if quiz.status != Quiz.Status.PUBLISHED:
        raise ValidationError({'quiz': 'This quiz is not available.'})
    if not quiz_assigned_to_student(quiz, user):
        raise ValidationError({'quiz': 'This quiz is not assigned to any of your classes.'})
    return quiz

def save_quiz_answer(attempt, answer_data):
    """Upserts one StudentQuizAnswer for `attempt`. Raises ValidationError
    on a malformed answer (wrong question, missing/invalid option, etc.)."""
    question_id = answer_data['question']
    try:
        question = attempt.quiz.questions.get(pk=question_id)
    except Question.DoesNotExist:
        raise ValidationError({'question': "This question does not belong to the attempt's quiz."})

    selected_option_id = answer_data.get('selected_option')
    matching_response = answer_data.get('matching_response') or {}

    if question.question_type == Question.Type.MATCHING:
        if not matching_response:
            raise ValidationError({'matching_response': 'This field is required for a matching question.'})
        selected_option_obj = None
    else:
        if not selected_option_id:
            raise ValidationError({'selected_option': 'This field is required for this question type.'})
        try:
            selected_option_obj = question.options.get(pk=selected_option_id)
        except QuestionOption.DoesNotExist:
            raise ValidationError({'selected_option': 'This option does not belong to the question.'})
        matching_response = {}

    StudentQuizAnswer.objects.update_or_create(
        attempt=attempt, question=question,
        defaults={
            'client_answer_id': answer_data['client_answer_id'],
            'selected_option': selected_option_obj,
            'matching_response': matching_response,
        },
    )

def sync_quiz_attempt(user, data):
    """Core logic behind offline batch sync of one quiz attempt. Used by
    both /api/quizzes/attempts/sync/ (Phase 8) and the generic
    /api/sync/push/ engine (Phase 11) — single source of truth, so tenant
    isolation and validation only need to be right in one place.

    `data`: validated dict with client_attempt_id, quiz, submitted, answers.

    Raises ValidationError (no row created) only for "quiz.attempt.id
    already belongs to someone else" or "quiz is invalid/inaccessible" —
    there's nothing valid to persist in either case. Once a valid quiz is
    resolved, this ALWAYS returns the attempt (never raises) — a bad
    answer marks it FAILED with `sync_error` set, rather than raising,
    so the caller can still report back what happened.
    """
    client_attempt_id = data['client_attempt_id']
    existing = StudentQuizAttempt.objects.filter(client_attempt_id=client_attempt_id).first()
    if existing and existing.student_id != user.id:
        raise ValidationError({'client_attempt_id': 'This client_attempt_id is already in use.'})

    quiz = validate_quiz_for_attempt(data['quiz'], user)

    with transaction.atomic():
        if existing:
            attempt = existing
        else:
            attempt = StudentQuizAttempt.objects.create(
                school=quiz.school, quiz=quiz, student=user,
                client_attempt_id=client_attempt_id, status=StudentQuizAttempt.Status.SYNC_PENDING,
            )
        attempt.status = StudentQuizAttempt.Status.SYNC_PENDING
        attempt.save(update_fields=['status', 'updated_at'])

        sync_errors = []
        for answer_data in data.get('answers', []):
            try:
                save_quiz_answer(attempt, answer_data)
            except ValidationError as exc:
                sync_errors.append({'question': answer_data.get('question'), 'error': exc.detail})

        if sync_errors:
            attempt.status = StudentQuizAttempt.Status.FAILED
            attempt.sync_error = str(sync_errors)
            attempt.save(update_fields=['status', 'sync_error', 'updated_at'])
            attempt.refresh_from_db()
            return attempt

        if data.get('submitted'):
            attempt.status = StudentQuizAttempt.Status.SYNCED
            if not attempt.submitted_at:
                attempt.submitted_at = timezone.now()
        else:
            attempt.status = StudentQuizAttempt.Status.IN_PROGRESS
        attempt.sync_error = ''
        attempt.save(update_fields=['status', 'submitted_at', 'sync_error', 'updated_at'])

        if data.get('submitted'):
            attempt = QuizMarkingService.mark_attempt(attempt)

    return attempt
