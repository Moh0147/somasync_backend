from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from apps.schools.models import School

User = get_user_model()

# Roles the system admin may NOT assign via the user-management API.
_FORBIDDEN_ROLES = {User.Role.ADMIN}


class UserSerializer(serializers.ModelSerializer):
    school_name = serializers.SerializerMethodField()

    def get_school_name(self, obj):
        return obj.school.name if obj.school_id else ''

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name',
            'phone_number', 'role', 'admin_scope',
            'school', 'school_name', 'is_active', 'date_joined',
        ]
        read_only_fields = fields


class CreateUserSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField(required=False, allow_blank=True, default='')
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default='')
    phone_number = serializers.CharField(max_length=20, required=False, allow_blank=True, default='')
    password = serializers.CharField(write_only=True, min_length=8)
    role = serializers.ChoiceField(choices=[
        c for c in User.Role.choices if c[0] not in _FORBIDDEN_ROLES
    ])
    school = serializers.PrimaryKeyRelatedField(queryset=School.objects.all(), required=False, allow_null=True, default=None)

    def validate(self, data):
        role = data['role']
        school = data.get('school')
        _all_school_roles = User._SCHOOL_REQUIRED_ROLES | User._SCHOOL_OPTIONAL_MARKING_ROLES
        if role in User._SCHOOL_REQUIRED_ROLES and not school:
            raise serializers.ValidationError({'school': f'School is required for role {role}.'})
        if role not in _all_school_roles and school:
            raise serializers.ValidationError({'school': 'School must be empty for this role.'})
        return data


class UpdateUserSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=[
        c for c in User.Role.choices if c[0] not in _FORBIDDEN_ROLES
    ], required=False)
    school = serializers.PrimaryKeyRelatedField(queryset=School.objects.all(), required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    phone_number = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate(self, data):
        instance = self.context.get('instance')
        role = data.get('role', instance.role if instance else None)
        # If school is being set/cleared, check against the resulting role.
        if 'school' in data:
            school = data['school']
            if role in User._SCHOOL_REQUIRED_ROLES and not school:
                raise serializers.ValidationError({'school': f'School is required for role {role}.'})
        return data


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        data = super().validate(attrs)
        user = self.user
        data['user'] = UserSerializer(user).data
        return data


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()
