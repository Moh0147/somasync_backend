"""Marking audit log utilities.

All functions are best-effort — they never raise, so an audit write
failure can never block the primary marking action.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.http import HttpRequest
    from .models import Exam, ExamScript


def _get_client_ip(request: HttpRequest) -> str | None:
    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded_for:
        return forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def log_marking_action(
    request: HttpRequest,
    action: str,
    *,
    script: ExamScript | None = None,
    exam: Exam | None = None,
    detail: dict | None = None,
) -> None:
    """Append a MarkingAuditLog entry. Never raises."""
    try:
        from .models import MarkingAuditLog
        user = request.user
        resolved_exam = exam or (script.exam if script else None)
        MarkingAuditLog.objects.create(
            exam=resolved_exam,
            script=script,
            actor=user if user.is_authenticated else None,
            actor_username=user.username if user.is_authenticated else '',
            actor_role=getattr(user, 'role', ''),
            action=action,
            detail=detail or {},
            ip_address=_get_client_ip(request),
        )
    except Exception:
        pass
