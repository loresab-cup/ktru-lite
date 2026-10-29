"""Shared HTTP lookup, serialization and query escaping."""

import uuid
from typing import Annotated

from fastapi import HTTPException
from pydantic import BeforeValidator
from sqlalchemy import select
from sqlalchemy.orm import Session

import access
import parser
import runtime as rt
import search
from models import Item, Job

ITEM_ID_MAX = 2**31 - 1  # items.id is a PostgreSQL integer: a larger number is no row at all


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
        elif 0 < int(key) <= ITEM_ID_MAX:
            query = select(Item).join(Job).where(Item.id == key, access.job_scope())
        else:
            raise ValueError(key)
        obj = session.scalar(query)
    except ValueError:
        obj = None
    if obj is None:
        raise HTTPException(404, f"{what} не найдено")
    return obj


def without_control(value):
    """Typed text loses control characters before its length is checked: the database refuses
    NUL and the export refuses the rest."""
    return parser.CONTROL.sub("", value) if isinstance(value, str) else value


Text = Annotated[str, BeforeValidator(without_control)]


def require_catalog() -> search.Catalog:
    if rt.state.catalog is None:
        raise HTTPException(503, "Каталог КТРУ ещё загружается, подождите минуту")
    return rt.state.catalog


def like_pattern(q: str) -> str:
    q = parser.CONTROL.sub("", q)  # PostgreSQL refuses NUL even in a search
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
