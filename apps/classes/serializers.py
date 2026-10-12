from rest_framework import serializers

from .models import SchoolClass

class SchoolClassSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolClass
        fields = [
            'id', 'school', 'name', 'class_level', 'academic_year',
            'class_code', 'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'class_code', 'created_at', 'updated_at']
        extra_kwargs = {'school': {'required': False}}
        validators = []

class JoinClassSerializer(serializers.Serializer):
    class_code = serializers.CharField(max_length=12)

class AssignMemberSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
