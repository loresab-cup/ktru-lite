"""Position details, manual corrections, acceptance and undo."""

import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import (
    func,
    select,
)
from sqlalchemy.orm import Session

import chooser
import confidence
import database as db
import embedder
import privacy
import runtime as rt
from decisions import forget, remember
from models import Item, Job
from pipeline import apply_candidates
from routes.common import ITEM_ID_MAX, Text, get_or_404, require_catalog, to_dict

router = APIRouter()


ORIGINAL_FIELDS = ("ktru_code", "ktru_name", "confidence", "zone", "status", "summary")


def restore_fields(item: Item, snapshot: dict) -> None:
    """Old snapshots may predate selected-code confidence; recover only supported proposals."""
    for field, value in snapshot.items():
        setattr(item, field, value)
    if item.confidence is None and item.status in ("auto", "needs_review"):
        score = confidence.for_code(item.candidates or [], item.ktru_code)
        if score is not None:
            item.confidence, item.zone = score, confidence.zone_of(score)


def remember_original(item: Item) -> None:
    """Keeps what the system proposed, once, before a person accepts or changes the code."""
    attributes = item.attributes or {}
    if "original" in attributes or item.status not in ("auto", "needs_review"):
        return
    original = {field: getattr(item, field) for field in ORIGINAL_FIELDS}
    item.attributes = {**attributes, "original": original}  # a new dict: JSON is not tracked


HISTORY_LIMIT = 10  # steps of "return the previous code"


def push_history(item: Item) -> None:
    """Keeps the code the item had before a person changes it, to bring it back later."""
    attributes = item.attributes or {}
    snapshot = {field: getattr(item, field) for field in ORIGINAL_FIELDS}
    history = [*attributes.get("history", []), snapshot][-HISTORY_LIMIT:]
    item.attributes = {**attributes, "history": history}


def restore_original(item: Item) -> None:
    attributes = dict(item.attributes or {})
    attributes.pop("history", None)  # back to the proposal of the system: the steps are gone
    original = attributes.pop("original", None)
    if original is None:  # accepted before the snapshot existed: the code did not change
        verdict = confidence.assess(item.candidates, is_product=bool(attributes.get("name")))
        original = {field: verdict[field] for field in ("confidence", "zone", "status")}
    restore_fields(item, original)
    item.attributes = attributes


def revert_decision(session: Session, item: Item) -> None:
    """Restore the proposal and withdraw its remembered human decision together."""
    restore_original(item)
    forget(session, item)


DIFFERENCES_SHOWN = 4  # characteristics per candidate: enough to tell neighbour codes apart


def with_differences(candidates: list[dict]) -> list[dict]:
    """Each candidate with the characteristics whose values differ between the candidates:
    five «Мыло туалетное твердое» in a row are told apart by «Масса нетто» and the like."""
    if len(candidates) < 2:
        return candidates
    differing = chooser.differing(candidates)
    return [
        c | {"differs": list(d["characteristics"].items())[:DIFFERENCES_SHOWN]}
        for c, d in zip(candidates, differing, strict=True)
    ]


@router.get("/api/items/{item_id}")
def get_item(item_id: int):
    """One position with its place in the job: the page of a code walks to the neighbours."""
    with db.SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        in_job = Item.job_id == item.job_id

        def count(*conditions) -> int:
            return session.scalar(select(func.count()).select_from(Item).where(in_job, *conditions))

        def edge(pick, *conditions) -> int | None:
            return session.scalar(select(pick(Item.id)).where(in_job, *conditions))

        # Review walks the doubtful rows and wraps to the beginning of the job
        not_reviewed = Item.status.in_(("needs_review", "failed"))
        next_review = edge(func.min, Item.id > item.id, not_reviewed)
        if next_review is None:
            next_review = edge(func.min, Item.id < item.id, not_reviewed)
        shown = to_dict(item)
        shown["candidates"] = with_differences(confidence.with_confidences(item.candidates or []))
        return shown | {
            "position": count(Item.id <= item.id),
            "total": count(),
            "prev_id": edge(func.max, Item.id < item.id),
            "next_id": edge(func.min, Item.id > item.id),
            "next_unreviewed_id": next_review,
            "same_pending": count(*same_rows(item)),
        }


def same_rows(item: Item) -> tuple:
    """Conditions for the other rows of the job with the same text that still wait for a look."""
    return (
        Item.job_id == item.job_id,
        Item.id != item.id,
        Item.raw_text == item.raw_text,
        Item.status.in_(("needs_review", "failed")),
    )


@router.post("/api/items/{item_id}/apply-same")
def apply_same(item_id: int):
    """The code a person gave this row goes to the identical rows of the job that wait."""
    with db.SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        if item.status not in ("accepted", "corrected") or not item.ktru_code:
            raise HTTPException(400, "Сначала примите или исправьте код этой позиции")
        others = session.scalars(select(Item).where(*same_rows(item))).all()
        for other in others:
            remember_original(other)
            if other.ktru_code == item.ktru_code:
                other.status = "accepted"
                continue
            push_history(other)
            other.ktru_code, other.ktru_name = item.ktru_code, item.ktru_name
            other.status, other.confidence, other.zone = "corrected", None, "low"
            other.summary = f"Код взят из такой же строки {item.row_number}: выбран человеком"
        session.commit()
        return {"applied": len(others)}


class Correction(BaseModel):
    ktru_code: str = Field(min_length=1, max_length=32)


@router.patch("/api/items/{item_id}")
def correct_item(item_id: int, body: Correction):
    # "10.12.10.000- 00000005" from a spreadsheet: spaces and line breaks are dropped
    code = require_catalog().get(re.sub(r"\s+", "", body.ktru_code))
    if code is None:
        raise HTTPException(400, "Такого кода нет в каталоге КТРУ")
    with db.SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        remember_original(item)
        push_history(item)
        item.ktru_code, item.ktru_name, item.status = code["code"], code["name"], "corrected"
        # A person's choice is not a score of the search: no made-up 100 %
        item.confidence, item.zone = None, "low"
        item.summary = "Код выбран вручную. Алгоритмическая уверенность не оценивалась"
        remember(session, item)
        session.commit()
        return to_dict(item)


class CharacteristicIn(BaseModel):
    model_config = {"str_strip_whitespace": True}
    name: Text = Field("", max_length=100)
    value: Text = Field(min_length=1, max_length=100)
    unit: Text = Field("", max_length=30)


class AttributesIn(BaseModel):
    model_config = {"str_strip_whitespace": True}
    name: Text = Field(min_length=1, max_length=300)
    brand: Text = Field("", max_length=100)
    model: Text = Field("", max_length=100)
    characteristics: list[CharacteristicIn] = Field(default_factory=list, max_length=30)


def manual_search_text(body: AttributesIn) -> str:
    """The query is built like the network's search_text: name, then «name: value unit»."""
    parts = [body.name]
    for c in body.characteristics:
        value = f"{c.value} {c.unit}".strip()
        parts.append(f"{c.name}: {value}" if c.name else value)
    return ". ".join(parts)


@router.put("/api/items/{item_id}/attributes")
def edit_attributes(item_id: int, body: AttributesIn):
    """A person fixes the attributes; the code is searched again by the fixed text."""
    catalog = require_catalog()
    query = manual_search_text(body)
    with db.SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        include_inactive = session.get(Job, item.job_id).include_inactive
        if rt.AUTH_REQUIRED:
            privacy.require_processing(session, session.get(Job, item.job_id).owner_id)
        vectors = embedder.embed([query])
        [found] = catalog.search(vectors, [query], include_inactive)
        remember_original(item)  # "revert" brings back what the system proposed
        push_history(item)
        old = item.attributes or {}
        # "fallback" stays: the row still counts as one the network did not answer
        keep = ("columns", "original", "history", "fallback")
        kept = {key: old[key] for key in keep if key in old}
        attributes = body.model_dump() | {"search_text": query, "source": "manual"}
        if vectors is None:
            attributes["words_only"] = True
        item.attributes = attributes | kept
        apply_candidates(item, found)
        item.status = "corrected"  # a person has looked at the row, whatever the new confidence
        item.summary = f"Атрибуты исправлены вручную. {item.summary}"
        session.commit()
        return to_dict(item)


@router.post("/api/items/{item_id}/accept")
def accept_item(item_id: int):
    with db.SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        if not item.ktru_code:
            raise HTTPException(400, "У позиции нет кода: выберите его вручную")
        remember_original(item)
        if item.status in ("needs_review", "failed"):
            remember(session, item)  # a person looked at a doubtful row and agreed
        item.status = "accepted"
        session.commit()
        return to_dict(item)


@router.post("/api/items/{item_id}/undo-code")
def undo_code(item_id: int):
    """Brings back the code the item had before the last manual change (also the old status)."""
    with db.SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        attributes = dict(item.attributes or {})
        history = list(attributes.get("history", []))
        if not history:
            raise HTTPException(400, "Предыдущего кода нет")
        restore_fields(item, history.pop())
        item.attributes = attributes | {"history": history}
        forget(session, item)
        if item.status == "corrected":
            remember(session, item)  # the code before was a person's too
        session.commit()
        return to_dict(item)


@router.post("/api/items/{item_id}/revert")
def revert_item(item_id: int):
    """Undoes accept or a manual code: the code the system proposed and its status come back."""
    with db.SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        if item.status == "failed":
            raise HTTPException(400, "Позицию не удалось обработать: вернуть нечего")
        if item.status in ("accepted", "corrected"):
            revert_decision(session, item)
            session.commit()
        return to_dict(item)


@router.post("/api/jobs/{job_id}/accept")
def accept_job(job_id: str, min_confidence: float = Query(85.0, ge=0, le=100)):
    catalog = require_catalog()
    with db.SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        items = session.scalars(
            select(Item).where(
                Item.job_id == job.id,
                Item.status.in_(("auto", "needs_review")),
                Item.confidence >= min_confidence,
            )
        ).all()
        accepted = 0
        for item in items:
            code = catalog.get(item.ktru_code or "")
            # A row that does not look like a product never goes to auto: not in bulk either
            is_product = bool((item.attributes or {}).get("name"))
            if code and code["status"] == "ACTIVE" and is_product:
                remember_original(item)
                item.status, accepted = "accepted", accepted + 1
        session.commit()
        return {"accepted": accepted}


class RevertIn(BaseModel):
    ids: list[Annotated[int, Field(ge=1, le=ITEM_ID_MAX)]] = Field(
        default_factory=list, max_length=1000
    )
    all_accepted: bool = False  # every accepted row of the job, not only the listed ones


@router.post("/api/jobs/{job_id}/revert")
def revert_items(job_id: str, body: RevertIn):
    """Returns accepted or corrected rows to review: the code the system proposed comes back."""
    with db.SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        if body.all_accepted:
            conditions = [Item.status == "accepted"]
        else:
            conditions = [Item.status.in_(("accepted", "corrected")), Item.id.in_(body.ids)]
        items = session.scalars(select(Item).where(Item.job_id == job.id, *conditions)).all()
        for item in items:
            revert_decision(session, item)
        session.commit()
        return {"reverted": len(items)}


MAX_LOOKUP = 500  # the catalog page lists many codes; a broad query is then worth narrowing
