"""Request identity propagated into FastAPI's worker threads; workers use persisted owners."""

from contextvars import ContextVar

from fastapi import HTTPException
from sqlalchemy import true

import runtime as rt
from models import Job

current_user: ContextVar[int | None] = ContextVar("current_user", default=None)


def owner_id() -> int | None:
    value = current_user.get()
    if rt.AUTH_REQUIRED and value is None:
        raise HTTPException(401, "Войдите в систему")
    return value


def job_scope():
    return Job.owner_id == owner_id() if rt.AUTH_REQUIRED else true()
