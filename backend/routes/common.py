"""Shared HTTP lookup, serialization and query escaping."""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import access
import runtime as rt
import search
from models import Item, Job


def to_dict(obj: Job | Item) -> dict:
    values = {
        column.name: getattr(obj, column.name)
        for column in obj.__table__.columns
        if column.name != "owner_id"
    }
    return {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in values.items()}


def get_or_404(session: Session, model, key, what: str):
    try:
        if model is Job:
            query = select(Job).where(Job.id == uuid.UUID(str(key)), access.job_scope())
        else:
            query = select(Item).join(Job).where(Item.id == key, access.job_scope())
        obj = session.scalar(query)
    except ValueError:
        obj = None
    if obj is None:
        raise HTTPException(404, f"{what} не найдено")
    return obj


def require_catalog() -> search.Catalog:
    if rt.state.catalog is None:
        raise HTTPException(503, "Каталог КТРУ ещё загружается, подождите минуту")
    return rt.state.catalog


def like_pattern(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
