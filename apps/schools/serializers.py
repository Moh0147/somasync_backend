from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import School

User = get_user_model()

class SchoolSerializer(serializers.ModelSerializer):
    class Meta:
        model = School
        fields = [
            'id', 'name', 'code', 'address', 'phone_number', 'email',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'is_active', 'created_at', 'updated_at']

class SchoolAdminCreateSerializer(serializers.ModelSerializer):
    """Creates a SCHOOL-scoped admin for a school. The `school` itself is
    never taken from the request body — it's injected from the URL-derived,
    permission-checked School instance via serializer context, so a client
    can't assign an admin to a different school than the one in the URL."""

    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'phone_number', 'password']

    def create(self, validated_data):
        password = validated_data.pop('password')
        school = self.context['school']
        user = User(
            role=User.Role.ADMIN,
            admin_scope=User.AdminScope.SCHOOL,
            school=school,
            **validated_data,
        )
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

class _SchoolStaffCreateSerializer(serializers.ModelSerializer):
    """Shared shape for TeacherCreateSerializer/StudentCreateSerializer --
    same fields and creation logic as SchoolAdminCreateSerializer, just a
    fixed `role` set by the subclass instead of always ADMIN. `school`
    still comes only from serializer context, never the request body."""

    password = serializers.CharField(write_only=True, min_length=8)
    role = None

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'phone_number', 'password']

    def create(self, validated_data):
        password = validated_data.pop('password')
        school = self.context['school']
        user = User(role=self.role, school=school, **validated_data)
        user.set_password(password)
        user.full_clean()
        user.save()
        return user

class TeacherCreateSerializer(_SchoolStaffCreateSerializer):
    role = User.Role.TEACHER

class StudentCreateSerializer(_SchoolStaffCreateSerializer):
    role = User.Role.STUDENT
