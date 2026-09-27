from django.core.exceptions import ValidationError
from django.db import models


class ClassLevel(models.Model):
    """A national curriculum level (Form I, Form II, ...). This is shared
    reference data, NOT school-scoped — Form I means the same thing at every
    school. Extensible later (e.g. Form V/VI for A-Level) by a system admin;
    never hard-coded as claims about official TIE/NECTA structure."""

    name = models.CharField(max_length=50, unique=True)
    code = models.CharField(max_length=20, unique=True)
    order = models.PositiveSmallIntegerField(unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.name


class Subject(models.Model):
    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='subjects')
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=20, blank=True)
    curriculum_code = models.CharField(
        max_length=50, blank=True,
        help_text='Optional reference code for future curriculum mapping — not an official TIE/NECTA code.',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['school', 'name'], name='unique_subject_name_per_school'),
        ]
        ordering = ['name']

    def __str__(self):
        return f'{self.name} ({self.school.code})'


class Topic(models.Model):
    """A topic belongs to a (Subject, ClassLevel) pair: e.g. "Algebra" under
    Mathematics / Form II. `school` is denormalized from `subject.school`
    for direct, efficient tenant-scoped queryset filtering — kept in sync
    and validated at save time (see TopicViewSet)."""

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='topics')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name='topics')
    class_level = models.ForeignKey(ClassLevel, on_delete=models.PROTECT, related_name='topics')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    curriculum_code = models.CharField(max_length=50, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['subject', 'class_level', 'title'], name='unique_topic_per_subject_level',
            ),
        ]
        ordering = ['class_level__order', 'order', 'title']

    def clean(self):
        super().clean()
        if self.subject_id and self.school_id and self.subject.school_id != self.school_id:
            raise ValidationError('A topic must belong to the same school as its subject.')

    def __str__(self):
        return f'{self.subject.name} / {self.class_level.name} / {self.title}'


class LearningObjective(models.Model):
    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='learning_objectives')
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name='learning_objectives')
    description = models.TextField()
    competency_code = models.CharField(max_length=50, blank=True)
    assessment_reference = models.CharField(max_length=100, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['order', 'id']

    def clean(self):
        super().clean()
        if self.topic_id and self.school_id and self.topic.school_id != self.school_id:
            raise ValidationError('A learning objective must belong to the same school as its topic.')

    def __str__(self):
        return f'{self.topic.title}: {self.description[:40]}'
