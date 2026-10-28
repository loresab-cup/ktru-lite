"""Public legal metadata and authenticated consent history updates."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import auth
import database as db
import privacy
import runtime as rt

router = APIRouter()


@router.get("/api/legal")
def legal_info():
    return privacy.public_info(rt.SESSION_HOURS)


class ConsentIn(BaseModel):
    model_config = {"extra": "forbid"}
    purpose: Literal["account", "files", "improvement"]
    accepted: bool
    version: str = Field(max_length=32)


@router.get("/api/auth/consents")
def get_consents(request: Request):
    with db.SessionLocal() as session:
        user = auth.user_by_token(session, request.cookies.get(rt.COOKIE, ""))
        if user is None:
            raise HTTPException(401, "Войдите в систему")
        return privacy.state(session, user.id)


@router.post("/api/auth/consents")
def set_consent(body: ConsentIn, request: Request):
    with db.SessionLocal() as session:
        user = auth.locked_user(session, request.cookies.get(rt.COOKIE, ""))
        if user is None:
            raise HTTPException(401, "Войдите в систему")
        try:
            return privacy.record(session, user.id, body.purpose, body.accepted, body.version)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
