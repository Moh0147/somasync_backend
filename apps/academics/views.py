from rest_framework import viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from common.mixins import EnvelopeMixin, TenantOwnedViewSetMixin
from common.permissions import IsSystemAdmin

from .models import ClassLevel, LearningObjective, Subject, Topic
from .serializers import (
    ClassLevelSerializer, LearningObjectiveSerializer, SubjectSerializer, TopicSerializer,
)

class ClassLevelViewSet(EnvelopeMixin, viewsets.ModelViewSet):

    queryset = ClassLevel.objects.all()
    serializer_class = ClassLevelSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']
    success_messages = {'create': 'Class level created', 'update': 'Class level updated'}

    def get_permissions(self):
        if self.action in ('create', 'update', 'partial_update'):
            return [IsAuthenticated(), IsSystemAdmin()]
        return [IsAuthenticated()]

class SubjectViewSet(EnvelopeMixin, TenantOwnedViewSetMixin, viewsets.ModelViewSet):

    queryset = Subject.objects.select_related('school')
    serializer_class = SubjectSerializer
    success_messages = {'create': 'Subject created', 'update': 'Subject updated'}

    def _validate_unique_name(self, school, name, exclude_pk=None):
        qs = Subject.objects.filter(school=school, name=name)
        if exclude_pk is not None:
            qs = qs.exclude(pk=exclude_pk)
        if qs.exists():
            raise ValidationError({'name': 'A subject with this name already exists for this school.'})

    def perform_create(self, serializer):
        school = self.resolve_create_school(serializer)
        self._validate_unique_name(school, serializer.validated_data.get('name'))
        serializer.save(school=school)

    def perform_update(self, serializer):
        school = serializer.instance.school
        name = serializer.validated_data.get('name', serializer.instance.name)
        self._validate_unique_name(school, name, exclude_pk=serializer.instance.pk)
        serializer.save(school=school)

class TopicViewSet(EnvelopeMixin, TenantOwnedViewSetMixin, viewsets.ModelViewSet):

    queryset = Topic.objects.select_related('school', 'subject', 'class_level')
    serializer_class = TopicSerializer
    success_messages = {'create': 'Topic created', 'update': 'Topic updated'}

    def _validate_subject_school(self, school, subject):
        if subject.school_id != school.id:
            raise ValidationError({'subject': 'This subject does not belong to the target school.'})

    def perform_create(self, serializer):
        school = self.resolve_create_school(serializer)
        subject = serializer.validated_data.get('subject')
        self._validate_subject_school(school, subject)
        serializer.save(school=school)

    def perform_update(self, serializer):
        school = serializer.instance.school
        subject = serializer.validated_data.get('subject', serializer.instance.subject)
        self._validate_subject_school(school, subject)
        serializer.save(school=school)

class LearningObjectiveViewSet(EnvelopeMixin, TenantOwnedViewSetMixin, viewsets.ModelViewSet):

    queryset = LearningObjective.objects.select_related('school', 'topic')
    serializer_class = LearningObjectiveSerializer
    success_messages = {'create': 'Learning objective created', 'update': 'Learning objective updated'}

    def _validate_topic_school(self, school, topic):
        if topic.school_id != school.id:
            raise ValidationError({'topic': 'This topic does not belong to the target school.'})

    def perform_create(self, serializer):
        school = self.resolve_create_school(serializer)
        topic = serializer.validated_data.get('topic')
        self._validate_topic_school(school, topic)
        serializer.save(school=school)

    def perform_update(self, serializer):
        school = serializer.instance.school
        topic = serializer.validated_data.get('topic', serializer.instance.topic)
        self._validate_topic_school(school, topic)
        serializer.save(school=school)
