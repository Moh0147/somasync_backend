"""Maps a sync `entity_type` string to its push/pull handlers. Both
registered types here (quiz_attempt, lesson_progress) reuse the exact same
service functions as their own dedicated, already-tested endpoints
(/api/quizzes/attempts/sync/, /api/progress/track/) — this registry is a
thin dispatch layer, not a second implementation of the underlying logic.

Adding a new syncable entity type means adding one push handler and
(optionally) one pull handler here — nothing else in this app changes.
"""

from apps.progress.serializers import LessonProgressSerializer, TrackProgressSerializer
from apps.progress.services import track_lesson_progress
from apps.quizzes.models import StudentQuizAttempt
from apps.quizzes.serializers import StudentQuizAttemptSerializer, SyncAttemptSerializer
from apps.quizzes.services import sync_quiz_attempt

def _push_quiz_attempt(user, client_id, data):
    payload = {**data, 'client_attempt_id': client_id}
    serializer = SyncAttemptSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    attempt = sync_quiz_attempt(user, serializer.validated_data)
    if attempt.status == StudentQuizAttempt.Status.FAILED:
        return False, StudentQuizAttemptSerializer(attempt).data, attempt.sync_error
    return True, StudentQuizAttemptSerializer(attempt).data, ''

def _pull_quiz_attempts(user, since):
    queryset = StudentQuizAttempt.objects.filter(student=user).select_related('school', 'quiz', 'student')
    if since:
        queryset = queryset.filter(updated_at__gt=since)
    return StudentQuizAttemptSerializer(queryset, many=True).data

def _push_lesson_progress(user, _client_id, data):
    serializer = TrackProgressSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    progress, _ = track_lesson_progress(user, serializer.validated_data)
    return True, LessonProgressSerializer(progress).data, ''

def _pull_lesson_progress(user, since):
    from apps.progress.models import LessonProgress
    queryset = LessonProgress.objects.filter(student=user).select_related('school', 'student', 'lesson')
    if since:
        queryset = queryset.filter(updated_at__gt=since)
    return LessonProgressSerializer(queryset, many=True).data

PUSH_HANDLERS = {
    'quiz_attempt': _push_quiz_attempt,
    'lesson_progress': _push_lesson_progress,
}

PULL_HANDLERS = {
    'quiz_attempt': _pull_quiz_attempts,
    'lesson_progress': _pull_lesson_progress,
}
