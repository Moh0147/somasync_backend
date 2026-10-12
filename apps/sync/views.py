from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import views
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated

from common.permissions import IsStudent
from common.utils import api_response

from .models import SyncRecord
from .registry import PULL_HANDLERS, PUSH_HANDLERS
from .serializers import PushRequestSerializer

class SyncPushView(views.APIView):
    """POST /api/sync/push/ — batched, idempotent push of offline-generated
    changes. Every item is tracked by a SyncRecord keyed on
    (user, entity_type, client_id): pushing the same item twice is always
    safe (an already-SYNCED item short-circuits without reprocessing), and
    a previously FAILED item is automatically retried.

    Conflict rule: a push can only ever create/update data the pushing
    student owns (their own quiz attempt, their own lesson progress) —
    each handler enforces this the same way its dedicated endpoint always
    has. A push never overwrites server-authoritative fields (a quiz's
    content, an attempt's score) — those are computed server-side
    (QuizMarkingService) regardless of what the client sends.

    Performance: after days offline, a single push can carry dozens of
    items. The whole batch commits as one transaction (one round trip to
    disk) rather than one commit per item's writes -- a real difference on
    the low-end hardware/slow links this project targets. Each item still
    gets its own savepoint (a nested `atomic()`), so a DB-level failure on
    one item rolls back only that item, not the batch -- the "one bad item
    must not fail the whole batch" guarantee is unchanged.
    """

    permission_classes = [IsAuthenticated, IsStudent]

    def post(self, request):
        serializer = PushRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data['items']

        with transaction.atomic():
            results = [self._process_item(request.user, item) for item in items]
        return api_response(data={'results': results})

    def _process_item(self, user, item):
        entity_type = item['entity_type']
        client_id = item['client_id']
        data = item.get('data') or {}

        handler = PUSH_HANDLERS.get(entity_type)
        if handler is None:
            return {
                'client_id': client_id, 'status': SyncRecord.Status.FAILED,
                'error': f'Unknown entity_type "{entity_type}".',
            }

        record, created = SyncRecord.objects.get_or_create(
            user=user, entity_type=entity_type, client_id=client_id,
            defaults={'school': user.school},
        )

        if not created and record.status == SyncRecord.Status.SYNCED:
            return {'client_id': client_id, 'status': record.status}

        record.attempts += 1

        try:
            with transaction.atomic():
                success, _result_data, error = handler(user, client_id, data)
        except ValidationError as exc:
            success, error = False, str(exc.detail)
        except Exception as exc:  # noqa: BLE001 -- one bad item must not fail the whole batch
            success, error = False, str(exc)

        record.status = SyncRecord.Status.SYNCED if success else SyncRecord.Status.FAILED
        record.error = '' if success else error
        record.save(update_fields=['status', 'error', 'attempts', 'updated_at'])

        result = {'client_id': client_id, 'status': record.status}
        if not success:
            result['error'] = error
        return result

class SyncPullView(views.APIView):
    """GET /api/sync/pull/?since=<ISO timestamp>&entity_type=<optional>
    Returns this student's own records (quiz attempts, lesson progress)
    updated since `since` (or everything, if omitted — an initial full
    sync). `entity_type` narrows to one registered type; omitted, all
    registered types are returned. Tenant isolation is inherited from each
    pull handler's own queryset, always filtered to `student=request.user`
    — a client can't pull another student's, or another school's, data by
    constructing a different query.
    """

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request):
        since_raw = request.query_params.get('since')
        since = None
        if since_raw:
            since = parse_datetime(since_raw)
            if since is None:
                raise ValidationError({'since': 'Must be a valid ISO 8601 timestamp.'})

        entity_type = request.query_params.get('entity_type')
        if entity_type:
            if entity_type not in PULL_HANDLERS:
                raise ValidationError({'entity_type': f'Unknown entity_type "{entity_type}".'})
            handlers = {entity_type: PULL_HANDLERS[entity_type]}
        else:
            handlers = PULL_HANDLERS

        results = {key: handler(request.user, since) for key, handler in handlers.items()}
        return api_response(data={'server_time': timezone.now(), 'results': results})
