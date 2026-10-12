import uuid

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.quizzes.models import Quiz, StudentQuizAttempt
from apps.quizzes.services import QuizMarkingService, save_quiz_answer

from .models import StudentSectionProgress

def section_statuses_for_student(lesson, student):
    """Ordered [{section, status, best_score, attempts}, ...] for one
    student's view of one lesson's sections. Side-effect-free (a GET must
    never write) -- a LOCKED/AVAILABLE section with no progress row yet is
    purely computed here, not persisted, until the student actually earns
    it via submit_section_quiz()."""
    sections = list(lesson.sections.select_related('mini_quiz').order_by('order'))
    if not sections:
        return []

    progress_by_section = {
        p.section_id: p for p in
        StudentSectionProgress.objects.filter(student=student, section__lesson=lesson)
    }

    first_section_id = sections[0].id
    results = []
    for section in sections:
        progress = progress_by_section.get(section.id)
        if progress is not None:
            status, best_score, attempts = progress.status, progress.best_score, progress.attempts
        elif section.id == first_section_id:
            status, best_score, attempts = StudentSectionProgress.Status.AVAILABLE, None, 0
        else:
            status, best_score, attempts = StudentSectionProgress.Status.LOCKED, None, 0
        results.append({'section': section, 'status': status, 'best_score': best_score, 'attempts': attempts})
    return results

def section_status_for_student(section, student):
    """Single-section version of the above, used by the content/
    submit-quiz access checks."""
    progress = StudentSectionProgress.objects.filter(student=student, section=section).first()
    if progress is not None:
        return progress.status
    first_section = section.lesson.sections.order_by('order').first()
    if first_section and first_section.id == section.id:
        return StudentSectionProgress.Status.AVAILABLE
    return StudentSectionProgress.Status.LOCKED

def ensure_section_accessible(section, student):
    if section_status_for_student(section, student) == StudentSectionProgress.Status.LOCKED:
        raise PermissionDenied('This section is locked -- complete the previous section first.')

def submit_section_quiz(student, section, answers_data):
    """Runs one mini-quiz attempt for `student` on `section`, entirely
    server-side and synchronous -- this is an always-online interaction
    (the student is actively reading the lesson), not part of the offline
    sync chain (Phase 11 handles that for the main quiz/lesson-progress
    flow). Marking is 100% deterministic via QuizMarkingService, exactly
    like every other quiz in this system -- AI is never involved in
    whether a section unlocks the next one.
    """
    ensure_section_accessible(section, student)

    mini_quiz = getattr(section, 'mini_quiz', None)
    if mini_quiz is None:
        raise ValidationError('This section has no mini-quiz.')
    if mini_quiz.quiz.status != Quiz.Status.PUBLISHED:
        raise ValidationError("This section's mini-quiz is not yet published.")

    with transaction.atomic():
        attempt = StudentQuizAttempt.objects.create(
            school=section.school, quiz=mini_quiz.quiz, student=student,
            client_attempt_id=f'section-{section.id}-{student.id}-{uuid.uuid4()}',
            status=StudentQuizAttempt.Status.SUBMITTED, submitted_at=timezone.now(),
        )
        for answer_data in answers_data:
            save_quiz_answer(attempt, {**answer_data, 'client_answer_id': str(uuid.uuid4())})
        attempt = QuizMarkingService.mark_attempt(attempt)

        passed = attempt.percentage >= mini_quiz.passing_score

        progress, _ = StudentSectionProgress.objects.get_or_create(
            student=student, section=section, defaults={'school': section.school},
        )
        progress.attempts += 1
        if progress.best_score is None or attempt.percentage > progress.best_score:
            progress.best_score = attempt.percentage

        next_section = None
        next_section_unlocked = False
        if passed:
            progress.status = StudentSectionProgress.Status.COMPLETED
            progress.completed_at = timezone.now()
            next_section = section.lesson.sections.filter(order__gt=section.order).order_by('order').first()
            if next_section is not None:
                next_progress, created = StudentSectionProgress.objects.get_or_create(
                    student=student, section=next_section,
                    defaults={'school': next_section.school, 'status': StudentSectionProgress.Status.AVAILABLE},
                )
                if not created and next_progress.status == StudentSectionProgress.Status.LOCKED:
                    next_progress.status = StudentSectionProgress.Status.AVAILABLE
                    next_progress.save(update_fields=['status', 'updated_at'])
                    next_section_unlocked = True
                else:
                    next_section_unlocked = created
        else:
            progress.status = StudentSectionProgress.Status.AVAILABLE
        progress.save(update_fields=['status', 'best_score', 'attempts', 'completed_at', 'updated_at'])

    wrong_answers = []
    if not passed:
        for answer in attempt.answers.select_related('question'):
            if not answer.is_correct:
                wrong_answers.append({
                    'question_id': answer.question_id,
                    'question_text': answer.question.text,
                    'explanation': answer.question.explanation,
                })

    return {
        'passed': passed,
        'score': attempt.percentage,
        'passing_score': mini_quiz.passing_score,
        'attempts': progress.attempts,
        'best_score': progress.best_score,
        'next_section_unlocked': next_section_unlocked,
        'next_section_id': next_section.id if next_section else None,
        'wrong_answers': wrong_answers,
    }
