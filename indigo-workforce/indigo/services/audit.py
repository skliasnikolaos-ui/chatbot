from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditLog


def log(db: Session, company_id: int | None, user_id: int | None, action: str, entity_type: str,
        entity_id=None, **details) -> None:
    """Append an audit entry (committed with the caller's transaction).

    Callers must not put pay amounts or personal data beyond identifiers into
    details; record which fields changed instead.
    """
    db.add(AuditLog(company_id=company_id, user_id=user_id, action=action, entity_type=entity_type,
                    entity_id=str(entity_id) if entity_id is not None else None, details=details))
