from rest_framework import serializers

from .models import LessonProgress


class LessonProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = LessonProgress
        fields = [
            'id', 'school', 'student', 'lesson', 'progress_percentage', 'completed',
            'last_position', 'last_accessed_at', 'updated_at',
        ]
        # All writes go through the `track` action's own input serializer
        # (TrackProgressSerializer) so `student`/`school` are never taken
        # from client input and the row is always an upsert, never a raw
        # create that could violate the unique (student, lesson) constraint.
        read_only_fields = fields


class TrackProgressSerializer(serializers.Serializer):
    lesson = serializers.IntegerField()
    progress_percentage = serializers.IntegerField(min_value=0, max_value=100, required=False, default=0)
    completed = serializers.BooleanField(required=False, default=False)
    last_position = serializers.CharField(required=False, allow_blank=True, default='')
    last_accessed_at = serializers.DateTimeField(required=False)
