from rest_framework import serializers

from .models import ClassLevel, LearningObjective, Subject, Topic


class ClassLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassLevel
        fields = ['id', 'name', 'code', 'order', 'is_active']
        read_only_fields = ['id']


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = [
            'id', 'school', 'name', 'code', 'curriculum_code', 'is_active',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        # `school` stays writable at the serializer level (a system admin
        # must supply it) but is never trusted as-is — see
        # TenantOwnedViewSetMixin.resolve_create_school / the viewsets below.
        extra_kwargs = {'school': {'required': False}}
        # DRF auto-generates a UniqueTogetherValidator from the model's
        # (school, name) UniqueConstraint, which would force `school` to be
        # "required" here — but a school admin never sends it (it's
        # injected after validation, once resolved). Uniqueness is checked
        # manually in SubjectViewSet instead, once `school` is known.
        validators = []


class TopicSerializer(serializers.ModelSerializer):
    class Meta:
        model = Topic
        fields = [
            'id', 'school', 'subject', 'class_level', 'title', 'description',
            'curriculum_code', 'order', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {'school': {'required': False}}


class LearningObjectiveSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningObjective
        fields = [
            'id', 'school', 'topic', 'description', 'competency_code',
            'assessment_reference', 'order', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {'school': {'required': False}}
