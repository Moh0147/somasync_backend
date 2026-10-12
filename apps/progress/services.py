from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.classes.models import StudentClassMembership
from apps.lessons.models import Lesson

from .models import LessonProgress

def lesson_assigned_to_student(lesson, student):
    class_ids = StudentClassMembership.objects.filter(student=student).values_list('school_class_id', flat=True)
    return lesson.assignments.filter(school_class_id__in=class_ids).exists()

def track_lesson_progress(user, data):
    """Core logic behind saving one student's lesson progress. Used by
    both /api/progress/track/ (Phase 10) and the generic /api/sync/push/
    engine (Phase 11) — single source of truth for tenant isolation and
    validation.

    `data`: validated dict with lesson, progress_percentage, completed,
    last_position, last_accessed_at.

    Returns (progress, created). Raises ValidationError if the lesson is
    invalid/inaccessible — there's nothing valid to persist in that case.
    """
    try:
        lesson = Lesson.objects.get(pk=data['lesson'])
    except Lesson.DoesNotExist:
        raise ValidationError({'lesson': 'No such lesson.'})

    if lesson.school_id != user.school_id:
        raise ValidationError({'lesson': 'No such lesson.'})
    if lesson.status != Lesson.Status.PUBLISHED:
        raise ValidationError({'lesson': 'This lesson is not available.'})
    if not lesson_assigned_to_student(lesson, user):
        raise ValidationError({'lesson': 'This lesson is not assigned to any of your classes.'})

    return LessonProgress.objects.update_or_create(
        student=user, lesson=lesson,
        defaults={
            'school': lesson.school,
            'progress_percentage': data.get('progress_percentage', 0),
            'completed': data.get('completed', False),
            'last_position': data.get('last_position', ''),
            'last_accessed_at': data.get('last_accessed_at') or timezone.now(),
        },
    )
