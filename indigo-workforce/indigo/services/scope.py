"""Company-scoped data access. All reads by id go through get_owned, which
returns 404 for rows of other companies (no existence leak)."""
from __future__ import annotations

from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

T = TypeVar("T")


class NotFound(Exception):
    pass


def get_owned(db: Session, model: type[T], obj_id, company_id: int) -> T:
    try:
        obj_id = int(obj_id)
    except (TypeError, ValueError):
        raise NotFound(model.__name__)
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "company_id", None) != company_id:
        raise NotFound(model.__name__)
    return obj


def scoped(model, company_id: int):
    return select(model).where(model.company_id == company_id)
