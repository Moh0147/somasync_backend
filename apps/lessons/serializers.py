from rest_framework import serializers

from .models import (
    Lesson, LessonAssignment, LessonAttachment, LessonSection, SectionContentBlock, SectionMiniQuiz,
)


class LessonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Lesson
        fields = [
            'id', 'school', 'subject', 'class_level', 'topic', 'learning_objective',
            'title', 'description', 'content', 'status', 'created_by',
            'published_at', 'created_at', 'updated_at',
        ]
        # status/published_at only change via the publish/archive actions —
        # never a freeform PATCH — so a lesson can't end up PUBLISHED
        # without published_at being set, or vice versa.
        read_only_fields = ['id', 'status', 'created_by', 'published_at', 'created_at', 'updated_at']
        extra_kwargs = {'school': {'required': False}}
        # Manual checks in the view instead (deferred `school` injection,
        # plus subject/topic/learning_objective relationship validation —
        # see Phase 4's SubjectSerializer for why the auto validator breaks
        # the deferred-injection pattern).
        validators = []


class LessonAttachmentSerializer(serializers.ModelSerializer):
    file_reference = serializers.SerializerMethodField()

    class Meta:
        model = LessonAttachment
        fields = [
            'id', 'file_reference', 'file_name', 'mime_type', 'file_size',
            'checksum', 'version', 'created_at',
        ]
        read_only_fields = fields

    def get_file_reference(self, obj):
        try:
            return obj.file.url
        except ValueError:
            return obj.file.name


class LessonAssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LessonAssignment
        fields = ['id', 'lesson', 'school_class', 'assigned_by', 'assigned_at']
        read_only_fields = fields


# --- mastery-based sections --------------------------------------------

class LessonSectionSerializer(serializers.ModelSerializer):
    mini_quiz_id = serializers.SerializerMethodField()

    class Meta:
        model = LessonSection
        fields = ['id', 'school', 'lesson', 'title', 'order', 'mini_quiz_id', 'created_at', 'updated_at']
        # `school` is always derived from `lesson.school` server-side
        # (LessonSectionViewSet.perform_create), consistent with Question's
        # relationship to its Quiz.
        read_only_fields = ['id', 'school', 'created_at', 'updated_at']

    def validate_title(self, value):
        title = value.strip()
        if not title:
            raise serializers.ValidationError('A section title cannot be blank.')
        return title

    def get_mini_quiz_id(self, obj):
        mini_quiz = getattr(obj, 'mini_quiz', None)
        return mini_quiz.id if mini_quiz else None


class SectionContentBlockSerializer(serializers.ModelSerializer):
    image_reference = serializers.SerializerMethodField()

    class Meta:
        model = SectionContentBlock
        fields = ['id', 'block_type', 'content', 'image', 'image_reference', 'order']
        read_only_fields = ['id', 'image_reference']

    def get_image_reference(self, obj):
        if not obj.image_id:
            return None
        try:
            return obj.image.file.url
        except ValueError:
            return obj.image.file.name


class SectionMiniQuizSerializer(serializers.ModelSerializer):
    class Meta:
        model = SectionMiniQuiz
        fields = ['id', 'section', 'quiz', 'passing_score', 'created_at', 'updated_at']
        # `quiz` is created and linked programmatically by the view (see
        # LessonSectionViewSet.mini_quiz) -- a teacher never picks an
        # arbitrary existing quiz here, avoiding one quiz being silently
        # shared/re-linked across sections behind the OneToOne's back.
        read_only_fields = ['id', 'section', 'quiz', 'created_at', 'updated_at']


class CreateSectionMiniQuizSerializer(serializers.Serializer):
    passing_score = serializers.IntegerField(required=False, default=70, min_value=0, max_value=100)


class SubmitSectionQuizAnswerSerializer(serializers.Serializer):
    question = serializers.IntegerField()
    selected_option = serializers.IntegerField(required=False, allow_null=True)
    matching_response = serializers.DictField(required=False)


class SubmitSectionQuizSerializer(serializers.Serializer):
    answers = SubmitSectionQuizAnswerSerializer(many=True)


class SectionStatusSerializer(serializers.Serializer):
    """Output shape for one row of GET /lessons/{id}/structure/ -- built
    from the plain dicts services.section_statuses_for_student() returns,
    not straight off the model, since `status`/`best_score`/`attempts` are
    per-student computed values, not section fields."""

    id = serializers.IntegerField(source='section.id')
    title = serializers.CharField(source='section.title')
    order = serializers.IntegerField(source='section.order')
    status = serializers.CharField()
    best_score = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    attempts = serializers.IntegerField()
    has_mini_quiz = serializers.SerializerMethodField()
    mini_quiz_id = serializers.SerializerMethodField()

    def get_has_mini_quiz(self, obj):
        return hasattr(obj['section'], 'mini_quiz')

    def get_mini_quiz_id(self, obj):
        mini_quiz = getattr(obj['section'], 'mini_quiz', None)
        return mini_quiz.id if mini_quiz else None
