from django.conf import settings
from django.db import models

class SyncRecord(models.Model):
    """Tracks the sync status of one pushed item, keyed by
    (user, entity_type, client_id) — the idempotency boundary. A push of
    the same client_id, for the same user and entity_type, is always safe
    to retry: an already-SYNCED record short-circuits without reprocessing;
    a FAILED one is retried (see SyncPushView).

    This is a framework-level audit/idempotency layer that sits ON TOP of
    each entity's own idempotency (StudentQuizAttempt.client_attempt_id,
    LessonProgress's (student, lesson) upsert) — it doesn't replace that,
    it tracks the *push operation itself*, uniformly across entity types.
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SYNCING = 'SYNCING', 'Syncing'
        SYNCED = 'SYNCED', 'Synced'
        FAILED = 'FAILED', 'Failed'

    school = models.ForeignKey('schools.School', on_delete=models.CASCADE, related_name='sync_records')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='sync_records')
    entity_type = models.CharField(max_length=50)
    client_id = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'entity_type', 'client_id'], name='unique_sync_record_per_user_entity_client',
            ),
        ]
        ordering = ['-updated_at']

    def __str__(self):
        return f'{self.user.username} - {self.entity_type}:{self.client_id} ({self.status})'
