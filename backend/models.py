"""Persistent procurement models."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from auth import User


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey(User.id, name="fk_jobs_owner_id_users", ondelete="SET NULL"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    total: Mapped[int] = mapped_column(default=0)
    processed: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    include_inactive: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage_seconds: Mapped[dict] = mapped_column(JSON, default=dict)


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int]
    raw_text: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    ktru_code: Mapped[str | None] = mapped_column(String(32))
    ktru_name: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None]
    zone: Mapped[str] = mapped_column(String(8), default="low")
    status: Mapped[str] = mapped_column(String(16), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")


class Decision(Base):
    """The code a person accepted or chose for a row text; the job it came from."""

    __tablename__ = "decisions"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # text_key of the row
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey(User.id, name="fk_decisions_owner_id_users", ondelete="CASCADE"), index=True
    )
    raw_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    ktru_code: Mapped[str] = mapped_column(String(32))
    ktru_name: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
