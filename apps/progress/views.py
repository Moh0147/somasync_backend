from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated

from common.mixins import EnvelopeMixin
from common.permissions import IsStudent, is_system_admin
from common.utils import api_response

from .models import LessonProgress
from .serializers import LessonProgressSerializer, TrackProgressSerializer
from .services import track_lesson_progress


class LessonProgressViewSet(EnvelopeMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """/api/progress/ — a student's progress through lessons. Read
    visibility: a student sees only their own; a teacher sees progress on
    lessons they created; an admin sees their school's; a system admin
    sees everything. All writes go through `track` (upsert), never a raw
    create/update, so the unique (student, lesson) constraint can never be
    violated by a client racing two creates."""

    queryset = LessonProgress.objects.select_related('school', 'student', 'lesson')
    serializer_class = LessonProgressSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if is_system_admin(user):
            return queryset
        if user.role == 'ADMIN':
            return queryset.filter(school_id=user.school_id)
        if user.role == 'TEACHER':
            return queryset.filter(lesson__created_by=user)
        if user.role == 'STUDENT':
            return queryset.filter(student=user)
        return queryset.none()

    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated, IsStudent])
    def track(self, request):
        serializer = TrackProgressSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        progress, created = track_lesson_progress(request.user, serializer.validated_data)
        return api_response(
            data=self.get_serializer(progress).data, message='Progress saved',
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
