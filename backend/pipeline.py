"""Extract attributes, find catalog candidates and process a specification."""

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import (
    delete,
    select,
    update,
)

import chooser
import confidence
import database as db
import embedder
import extractor
import parser
import runtime as rt
import search
from decisions import apply_decision, recalled, text_key
from job_store import cancelled, finish, set_job, upload_path
from models import Decision, Item, Job

logger = logging.getLogger("pipeline")


def tick(stages: dict, stage: str, started: float) -> None:
    stages[stage] = round(stages.get(stage, 0.0) + time.monotonic() - started, 2)


def full_cards(candidates: list) -> list[dict] | None:
    """Every kept characteristic of the codes, from the catalog in memory (None without it)."""
    catalog = rt.state.catalog
    if catalog is None or not hasattr(catalog, "index"):
        return None
    found = [catalog.index.get(c.get("code")) for c in candidates]
    if None in found:
        return None
    return [catalog.brief(i, full=True) for i in found]


def apply_candidates(item: Item, found: list, is_product: bool = True) -> None:
    """Top-5 -> the best code, confidence, zone, status and explanation of the item."""
    if not is_product:
        found = []
    elif (item.attributes or {}).get("words_only"):
        found = [c | {"words_only": True} for c in found]
    verdict = confidence.assess(found, is_product=is_product)
    best = found[0] if found else {}
    item.candidates = found
    item.ktru_code, item.ktru_name = best.get("code"), best.get("name")
    item.confidence, item.zone = verdict["confidence"], verdict["zone"]
    item.status, item.summary = verdict["status"], verdict["summary"]
    # Why a person has to look: kept with the attributes (a JSON column, no new table column)
    attributes = {k: v for k, v in (item.attributes or {}).items() if k != "review"}
    reason = ""
    if verdict["status"] != "auto":
        reason = confidence.review_reason(found, is_product, full_cards(found[:2]))
    item.attributes = attributes | ({"review": reason} if reason else {})


def build_item(
    job_id: uuid.UUID,
    row: parser.Item,
    attributes: dict,
    found: list,
    decision: Decision | None = None,
) -> Item:
    item = Item(
        job_id=job_id,
        row_number=row.row_number,
        raw_text=row.text,
        attributes={**attributes, "columns": row.columns},
    )
    try:
        apply_candidates(
            item,
            found,
            is_product=bool(attributes.get("name")) and any(c.isalpha() for c in row.text),
        )
        if decision is not None:
            apply_decision(item, decision)
    except Exception as exc:  # one broken row must not stop the job
        logger.exception("item failed")
        item.status, item.summary = "failed", f"Ошибка обработки позиции: {type(exc).__name__}"
    return item


def unit_hints(row: parser.Item, attributes: dict) -> tuple[str | None, str | None]:
    """Units of the row: the one of the «Ед. изм.» column and the one the network says it is
    bought in. The column is the customer's word and counts more; when the customer wrote a
    unit we do not know («Минута»), the guess of the network does not stand in for it."""
    column, written = None, False
    for title, value in row.columns.items():
        if not title.lower().startswith("ед"):
            continue
        written = written or bool(str(value).strip())
        column = search.unit_key(value)
        if column:
            break
    guessed = search.unit_key(attributes.get("measure") or "")
    return column, None if written and column is None else guessed


def process_items(
    job_id: uuid.UUID, rows: list[parser.Item], include_inactive: bool, stages: dict
) -> None:
    """One thread of the job prepares the batches in order (the network's answer, then the
    vectors), so the vectors of a batch are fetched while the next answers are awaited, and the
    search of a batch here does not hold the vectors of the next one. Measured 28.10.2026: the
    search stage of 1000 rows took 35-63 s, most of it the 20 vector requests a second."""
    llm = extractor.JobExtractor([row.text for row in rows], cancelled=rt.state.cancels.get(job_id))
    batches = [rows[start : start + rt.BATCH] for start in range(0, len(rows), rt.BATCH)]
    preparer = ThreadPoolExecutor(1, thread_name_prefix="prepare")
    try:
        prepared = [preparer.submit(prepare_batch, batch, llm, stages) for batch in batches]
        for n, batch in enumerate(batches):
            if cancelled(job_id):
                raise extractor.Cancelled
            started = time.monotonic()
            ready = prepared[n].result()
            tick(stages, "wait", started)
            process_batch(job_id, batch, n * rt.BATCH, ready, include_inactive, stages)
    except Exception:  # stopped or failed: the batches not yet sent are not needed
        llm.cancel()
        raise
    finally:
        preparer.shutdown(wait=False, cancel_futures=True)


def prepare_batch(batch: list[parser.Item], llm: extractor.JobExtractor, stages: dict) -> dict:
    """Attributes, search texts, units and vectors of a batch; runs in the preparing thread."""
    started = time.monotonic()
    attributes = [llm.get(row.text) for row in batch]
    tick(stages, "extract", started)
    started = time.monotonic()
    queries = [f"{a['search_text']}. {row.text}" for a, row in zip(attributes, batch, strict=True)]
    vectors = embedder.embed(queries)
    if vectors is None:  # no vectors: the page warns that these rows were found by words
        attributes = [a | {"words_only": True} for a in attributes]
    tick(stages, "embed", started)
    return {
        "attributes": attributes,
        "queries": queries,
        "hints": [unit_hints(row, a) for a, row in zip(attributes, batch, strict=True)],
        "vectors": vectors,
    }


def process_batch(
    job_id: uuid.UUID,
    batch: list[parser.Item],
    start: int,
    prepared: dict,
    include_inactive: bool,
    stages: dict,
) -> None:
    started = time.monotonic()
    hints, attributes = prepared["hints"], prepared["attributes"]
    found = rt.state.catalog.search(
        prepared["vectors"],
        prepared["queries"],
        include_inactive,
        units=[column or guessed for column, guessed in hints],
        column_units=[column for column, _ in hints],
        anchors=[row.text for row in batch],
        attributes=[attrs.get("characteristics") or [] for attrs in attributes],
    )
    tick(stages, "search", started)
    started = time.monotonic()
    known = recalled(batch, job_id)
    items = [
        build_item(job_id, row, attrs, cands, known.get(text_key(row.text)))
        for row, attrs, cands in zip(batch, attributes, found, strict=True)
    ]
    tick(stages, "confidence", started)
    started = time.monotonic()
    with db.SessionLocal() as session:
        settled = set(
            session.scalars(
                select(Item.row_number).where(
                    Item.job_id == job_id, Item.status.in_(("corrected", "accepted"))
                )
            )
        )
        session.add_all(item for item in items if item.row_number not in settled)
        session.commit()
    set_job(job_id, processed=start + len(batch), stage_seconds=dict(stages))
    tick(stages, "save", started)


def choose_codes(job_id: uuid.UUID, stages: dict, until: float) -> None:
    """Second opinion of a neural network for the rows the search was not sure about.

    An item changes only if it is still needs_review with the same code: edits made by the user
    while the job runs are never overwritten. The row stays for review; its confidence measures
    search support for the selected code against the strongest alternative."""
    if not chooser.ENABLED or extractor.llm_state() != "on":
        return
    started = time.monotonic()
    with db.SessionLocal() as session:
        items = session.scalars(
            select(Item).where(Item.job_id == job_id, Item.status == "needs_review")
        ).all()
    rows = [
        {"id": item.id, "text": item.raw_text, "candidates": item.candidates}
        for item in items
        if len(item.candidates) > 1
    ]
    cancelled = rt.state.cancels.get(job_id)
    picks = chooser.choose(rows, cancelled, until)
    with db.SessionLocal() as session:
        for item in items:
            code, provider = picks.get(item.id, (item.ktru_code, ""))
            if code == item.ktru_code:
                continue
            score = confidence.for_code(item.candidates, code)
            if score is None:  # Defensive: an invalid provider result must not change the row.
                continue
            chosen = next(c for c in item.candidates if c["code"] == code)
            summary = (
                f"Код выбран нейросетью ({provider}) из кандидатов поиска. "
                f"{confidence.explain(chosen, bool((item.attributes or {}).get('name')))}. "
                "Процент отражает поддержку выбранного кода поиском, а не самооценку нейросети"
            )
            # The reason was about the search's first code; now it is about the choice
            first = item.candidates[0]
            review = (
                f"Нейросеть выбрала этот код вместо первого кандидата поиска {first['code']} "
                f"«{first['name']}». Поиск и нейросеть расходятся: проверьте выбор"
            )
            session.execute(
                update(Item)
                .where(
                    Item.id == item.id,
                    Item.ktru_code == item.ktru_code,
                    Item.status == "needs_review",
                )
                .values(
                    ktru_code=code,
                    ktru_name=chosen["name"],
                    confidence=score,
                    zone=confidence.zone_of(score),
                    summary=summary[:1000],
                    attributes=(item.attributes or {}) | {"review": review},
                )
            )
        session.commit()
    tick(stages, "choose", started)
    if cancelled is not None and cancelled.is_set():
        raise extractor.Cancelled


def run_job(job_id: uuid.UUID) -> None:
    stages: dict = {}
    started = time.monotonic()
    with db.SessionLocal() as session:
        job = session.get(Job, job_id)
        if job is None:  # deleted between the claim and the start
            return
        path, include_inactive = upload_path(job), job.include_inactive
        # Redelivery may restart extraction, but must preserve a person's completed edits.
        session.execute(
            delete(Item).where(Item.job_id == job_id, Item.status.not_in(("corrected", "accepted")))
        )
        session.commit()
    rt.state.cancels[job_id] = threading.Event()
    try:
        rows = parser.parse_file(path)
        tick(stages, "read", started)
        set_job(job_id, total=len(rows))
        process_items(job_id, rows, include_inactive, stages)
        # Preserve the 1000-row budget and give larger files proportionally more review time.
        budget = rt.JOB_TIME_LIMIT * max(1, len(rows) / 1000)
        choose_codes(job_id, stages, until=started + budget)
        tick(stages, "total", started)
        status, error = "done", None
    except extractor.Cancelled:
        tick(stages, "total", started)
        status, error = "cancelled", None
    except parser.ParseError as exc:
        status, error = "failed", str(exc)
    except FileNotFoundError:
        status, error = "failed", "Файл задания не найден: загрузите его заново"
    except Exception:
        logger.exception("job %s failed", job_id)
        status, error = "failed", "Не удалось обработать файл, попробуйте ещё раз"
    # If this write fails (the database restarts), the file stays: after a restart the job is
    # queued again and processed from the start
    try:
        finish(job_id, status, error, stages)
    finally:
        rt.state.cancels.pop(job_id, None)
    path.unlink(missing_ok=True)
