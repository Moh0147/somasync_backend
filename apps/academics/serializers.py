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
        extra_kwargs = {'school': {'required': False}}
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
