"""Remember and withdraw human decisions for identical procurement rows."""

import hashlib
from datetime import UTC

from sqlalchemy import (
    delete,
    select,
)
from sqlalchemy.orm import Session

import database as db
import parser
import runtime as rt
from models import Decision, Item, Job, now


def text_key(text: str, owner_id: int | None = None) -> str:
    """The same row whatever its case, spaces and ё."""
    normalized = " ".join(text.lower().replace("ё", "е").split())
    scoped = f"user:{owner_id}:{normalized}" if owner_id is not None else normalized
    return hashlib.sha256(scoped.encode()).hexdigest()


def apply_decision(item: Item, decision: Decision) -> None:
    """The code a person gave the same row before, if it is still in force."""
    code = rt.state.catalog.get(decision.ktru_code) if hasattr(rt.state.catalog, "get") else None
    if not code or code.get("status") != "ACTIVE":
        return
    item.ktru_code, item.ktru_name = code["code"], code["name"]
    item.status, item.confidence, item.zone = "auto", None, "high"
    when = decision.decided_at
    when = when if when.tzinfo else when.replace(tzinfo=UTC)  # SQLite keeps it without the zone
    item.summary = f"Код из решения человека: эта строка уже проверялась. {item.summary}"
    attributes = {k: v for k, v in item.attributes.items() if k != "review"}
    item.attributes = attributes | {"remembered": when.isoformat()}


def recalled(rows: list[parser.Item], job_id=None) -> dict[str, Decision]:
    """Decisions of a person for the rows of a batch, by text_key; one query."""
    if not rt.REMEMBER:
        return {}
    with db.SessionLocal() as session:
        job = session.get(Job, job_id) if job_id is not None else None
        if rt.AUTH_REQUIRED and job is None:
            return {}
        owner = job.owner_id if job else None
        keys = {text_key(row.text, owner): text_key(row.text) for row in rows}
        found = session.scalars(
            select(Decision)
            .join(Job, Decision.job_id == Job.id)
            .where(
                Decision.key.in_(keys),
                Job.owner_id == owner,
            )
        ).all()
    return {keys[d.key]: d for d in found}


def remember(session: Session, item: Item) -> None:
    """Keeps what a person gave the row; the last decision for a text wins."""
    if rt.REMEMBER and item.ktru_code:
        job = session.get(Job, item.job_id)
        decision = Decision(
            key=text_key(item.raw_text, job.owner_id if job else None),
            job_id=item.job_id,
            ktru_code=item.ktru_code,
            ktru_name=item.ktru_name or "",
            decided_at=now(),
        )
        session.merge(decision)


def forget(session: Session, item: Item) -> None:
    """The decision this job made for the row text is taken back."""
    job = session.get(Job, item.job_id)
    session.execute(
        delete(Decision).where(
            Decision.key == text_key(item.raw_text, job.owner_id if job else None),
            Decision.job_id == item.job_id,
        )
    )
