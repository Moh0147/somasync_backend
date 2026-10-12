from rest_framework import serializers

from .models import AIExplanationRequest

class ExplainMistakeInputSerializer(serializers.Serializer):
    student_answer = serializers.IntegerField()
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
