from rest_framework import serializers

from .models import AIExplanationRequest


class ExplainMistakeInputSerializer(serializers.Serializer):
    student_answer = serializers.IntegerField()
    # Bounded so a client can't force an oversized prompt onto the shared
    # local Ollama instance -- a free-text field otherwise has no natural
    # limit, and this one is echoed straight into an LLM prompt.
    context = serializers.CharField(required=False, allow_blank=True, default='', max_length=1000)


class AIExplanationRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIExplanationRequest
        fields = [
            'id', 'school', 'student', 'student_answer', 'context', 'status',
            'explanation', 'correct_concept', 'concise_feedback', 'error',
            'attempts', 'created_at', 'updated_at',
        ]
        read_only_fields = fields
