"""Versioned, purpose-specific consent events; optional improvement is never implied."""

import os
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from auth import AuthBase, User

VERSION = "2026-10-29.2"  # .2: work without an account (guests)
PURPOSES = ("account", "files", "improvement")


class Consent(AuthBase):
    __tablename__ = "user_consents"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(20))
    version: Mapped[str] = mapped_column(String(32))
    accepted: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    __table_args__ = (Index("ix_user_consents_user_purpose_id", "user_id", "purpose", "id"),)


def state(session: Session, user_id: int) -> dict:
    latest = (
        select(func.max(Consent.id).label("last_id"))
        .where(Consent.user_id == user_id)
        .group_by(Consent.purpose)
        .subquery()
    )
    events = session.scalars(select(Consent).join(latest, Consent.id == latest.c.last_id)).all()
    result = dict.fromkeys(PURPOSES, False)
    for event in events:
        result[event.purpose] = event.accepted and event.version == VERSION
    return {"version": VERSION, **result}


def record(session: Session, user_id: int, purpose: str, accepted: bool, version: str):
    if purpose not in PURPOSES or version != VERSION:
        raise ValueError("Откройте актуальный текст согласия и подтвердите его заново")
    current = state(session, user_id)
    if purpose == "improvement" and accepted and not (current["account"] and current["files"]):
        raise ValueError("Сначала подтвердите обработку учётной записи и файлов")
    changes = {purpose: accepted}
    if not accepted and purpose == "account":
        changes.update(files=False, improvement=False)
    elif not accepted and purpose == "files":
        changes["improvement"] = False
    for changed, value in changes.items():
        session.add(Consent(user_id=user_id, purpose=changed, version=version, accepted=value))
    queued = []
    if purpose in ("account", "files") and not accepted:
        from models import Job

        queued = session.scalars(
            select(Job).where(Job.owner_id == user_id, Job.status == "queued")
        ).all()
        session.execute(
            update(Job)
            .where(Job.owner_id == user_id, Job.status.in_(("queued", "processing")))
            .values(status="cancelled", finished_at=datetime.now(UTC))
        )
    session.commit()
    if queued:
        from job_store import upload_path

        for job in queued:
            upload_path(job).unlink(missing_ok=True)
    return state(session, user_id)


def accept_entry(session: Session, user_id: int, version: str) -> None:
    """The checkbox of the entry page (guest, login, registration): the policy with the account
    and files purposes, each written as its own event; one already given in this version is not
    written again. The improvement purpose stays a separate choice of the profile."""
    current = state(session, user_id)
    for purpose in ("account", "files"):
        if not current[purpose]:
            record(session, user_id, purpose, True, version)


def require_processing(session: Session, user_id: int):
    current = state(session, user_id)
    if not current["account"] or not current["files"]:
        raise HTTPException(403, "Перед загрузкой подтвердите согласие на обработку файлов")


def public_info(session_hours: float) -> dict:
    providers = os.getenv("LLM_PROVIDERS", "yandex").split(",")
    names = []
    if any(p.strip().startswith("yandex") for p in providers) or os.getenv("YANDEX_EMBED_API_KEY"):
        names.append("Яндекс AI Studio")
    return {
        "version": VERSION,
        "operator": os.getenv("PRIVACY_OPERATOR_NAME", "Маслов К. Р."),
        "email": os.getenv("PRIVACY_CONTACT_EMAIL", "krm5@tpu.ru"),
        "address": os.getenv("PRIVACY_OPERATOR_ADDRESS", ""),
        "providers": names,
        "session_hours": session_hours,
    }
