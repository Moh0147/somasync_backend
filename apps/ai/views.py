from django.conf import settings
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated

from common.mixins import EnvelopeMixin
from common.permissions import IsStudent, is_system_admin
from common.throttling import AIRequestThrottle
from common.utils import api_response

from .explanation_service import generate_and_store, request_explanation
from .models import AIExplanationRequest
from .serializers import AIExplanationRequestSerializer, ExplainMistakeInputSerializer
from .services import get_ai_service


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def ai_health_check(request):
    """GET /api/ai/health/ — reports whether the configured AI backend is
    reachable. Never proxies a prompt or exposes the backend directly —
    just a boolean status, so the frontend can decide whether to show
    AI-dependent UI (Explain My Mistake, etc., Phase 13+) without ever
    talking to Ollama itself."""
    healthy = get_ai_service().health_check()
    return api_response(data={'healthy': healthy})


class AIExplanationRequestViewSet(
    EnvelopeMixin, mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet,
):
    """/api/ai/explain-mistake/ — a student asks for an explanation of
    their own wrong quiz answer. Served from MistakeExplanationCache when
    another student already triggered the same explanation; otherwise
    generated via the configured AIService (never a direct frontend link
    to Ollama). AI is never required for core learning/quiz completion —
    a FAILED request is recorded, not a hard error, and is retriable.

    Read visibility: a student sees only their own requests; a teacher
    sees requests tied to quizzes they created; an admin sees their
    school's; a system admin sees everything.
    """

    queryset = AIExplanationRequest.objects.select_related('school', 'student', 'student_answer')
    serializer_class = AIExplanationRequestSerializer
    success_messages = {'create': 'Explanation requested'}
    throttle_scope = 'ai_explain'

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if is_system_admin(user):
            return queryset
        if user.role == 'ADMIN':
            return queryset.filter(school_id=user.school_id)
        if user.role == 'TEACHER':
            return queryset.filter(student_answer__question__quiz__created_by=user)
        if user.role == 'STUDENT':
            return queryset.filter(student=user)
        return queryset.none()

    def get_permissions(self):
        if self.action in ('create', 'retry'):
            return [IsAuthenticated(), IsStudent()]
        return [IsAuthenticated()]

    def get_throttles(self):
        if self.action in ('create', 'retry') and settings.THROTTLING_ENABLED:
            return [AIRequestThrottle()]
        return []

    def create(self, request, *args, **kwargs):
        serializer = ExplainMistakeInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = request_explanation(
            request.user, serializer.validated_data['student_answer'], serializer.validated_data.get('context', ''),
        )
        return api_response(
            data=self.get_serializer(result).data, message=self.success_messages['create'],
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def retry(self, request, pk=None):
        explanation_request = self.get_object()
        if explanation_request.student_id != request.user.id:
            raise PermissionDenied('You may only retry your own request.')
        if explanation_request.status != AIExplanationRequest.Status.FAILED:
            raise ValidationError('Only a FAILED request can be retried.')

        updated = generate_and_store(explanation_request)
        return api_response(data=self.get_serializer(updated).data, message='Retried')
