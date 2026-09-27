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
        # is_active is changed only via the explicit activate/deactivate
        # actions, never through a generic update — keeps deactivation
        # an intentional, auditable action rather than an incidental PATCH.
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
