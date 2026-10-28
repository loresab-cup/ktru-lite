"""HTTP API, the job queue and the export.

Queue = the jobs table: WORKERS background threads take a queued job with
SELECT ... FOR UPDATE SKIP LOCKED and process it in batches of BATCH rows, updating `processed`.
Pipeline of a batch: extractor (LLM or rules) -> embedder (Yandex) -> search -> confidence.
"""

import csv
import hashlib
import io
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    String,
    Text,
    Uuid,
    case,
    create_engine,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, defer, mapped_column, sessionmaker

import auth
import chooser
import confidence
import embedder
import extractor
import parser
import search

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("api")

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://ktru:ktru@db:5432/ktru")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
WORKERS = int(os.getenv("WORKERS", "2"))
# Finished jobs older than this are deleted once an hour; 0 keeps them (the default)
KEEP_DAYS = float(os.getenv("JOBS_KEEP_DAYS", "0"))
CLEAN_EVERY = 3600.0
JOB_TIME_LIMIT = 270.0  # seconds: 1000 rows must be done within 5 minutes, with a margin
BATCH = 20  # small batches: finished rows reach the table while the rest are processed
POLL_SECONDS = 1.0
ITEM_STATUSES = ("auto", "needs_review", "corrected", "accepted", "failed")
# A code a person gave a row goes to the same row of later files (REMEMBER_DECISIONS=0 - off)
REMEMBER = os.getenv("REMEMBER_DECISIONS", "1") != "0"
# Accounts: every /api/ address except PUBLIC needs a login (AUTH_REQUIRED=0 - only for development)
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "1") != "0"
SESSION_HOURS = float(os.getenv("SESSION_HOURS", "12"))
COOKIE = "ktru_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"  # 1 behind HTTPS
PUBLIC = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/docs", "/api/openapi.json"}


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
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
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    ktru_code: Mapped[str] = mapped_column(String(32))
    ktru_name: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def text_key(text: str) -> str:
    """The same row whatever its case, spaces and ё."""
    return hashlib.sha256(" ".join(text.lower().replace("ё", "е").split()).encode()).hexdigest()


sqlite = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if sqlite else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class State:
    catalog: search.Catalog | None = None
    catalog_error: str = ""  # why the catalog is not loaded, for /api/health
    stop = threading.Event()

    def __init__(self) -> None:
        self.cancels: dict[uuid.UUID, threading.Event] = {}  # running jobs -> "stop" flag


state = State()

# --- queue ---------------------------------------------------------------------------------


def upload_path(job: Job) -> Path:
    return UPLOAD_DIR / f"{job.id}{Path(job.filename).suffix.lower()}"


def claim_job() -> uuid.UUID | None:
    with SessionLocal() as session:
        query = (
            select(Job.id)
            .where(Job.status == "queued")
            .order_by(Job.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        job_id = session.scalars(query).first()
        if job_id is None:
            return None
        claimed = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == "queued")
            .values(status="processing", started_at=now(), processed=0, total=0, error=None)
        ).rowcount
        session.commit()
        return job_id if claimed else None


def set_job(job_id: uuid.UUID, **values) -> None:
    with SessionLocal() as session:
        session.execute(update(Job).where(Job.id == job_id).values(**values))
        session.commit()


def finish(job_id: uuid.UUID, status: str, error: str | None, stages: dict) -> None:
    """Final status; a job the user cancelled meanwhile stays cancelled."""
    values = {"finished_at": now(), "stage_seconds": dict(stages)}
    with SessionLocal() as session:
        query = update(Job).where(Job.id == job_id)
        if status != "cancelled":
            query = query.where(Job.status == "processing")
        # The error goes only with its status: a job cancelled meanwhile shows no failure text
        session.execute(query.values(status=status, error=error, **values))
        session.execute(update(Job).where(Job.id == job_id).values(**values))
        session.commit()


def cancelled(job_id: uuid.UUID) -> bool:
    with SessionLocal() as session:
        return session.scalar(select(Job.status).where(Job.id == job_id)) == "cancelled"


def tick(stages: dict, stage: str, started: float) -> None:
    stages[stage] = round(stages.get(stage, 0.0) + time.monotonic() - started, 2)


def full_cards(candidates: list) -> list[dict] | None:
    """Every kept characteristic of the codes, from the catalog in memory (None without it)."""
    catalog = state.catalog
    if catalog is None or not hasattr(catalog, "index"):
        return None
    found = [catalog.index.get(c.get("code")) for c in candidates]
    if None in found:
        return None
    return [catalog.brief(i, full=True) for i in found]


def apply_candidates(item: Item, found: list, is_product: bool = True) -> None:
    """Top-5 -> the best code, confidence, zone, status and explanation of the item."""
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
        apply_candidates(item, found, is_product=bool(attributes.get("name")))
        if decision is not None:
            apply_decision(item, decision)
    except Exception as exc:  # one broken row must not stop the job
        logger.exception("item failed")
        item.status, item.summary = "failed", f"Ошибка обработки позиции: {type(exc).__name__}"
    return item


def apply_decision(item: Item, decision: Decision) -> None:
    """The code a person gave the same row before, if it is still in force."""
    code = state.catalog.get(decision.ktru_code) if hasattr(state.catalog, "get") else None
    if not code or code.get("status") != "ACTIVE":
        return
    item.ktru_code, item.ktru_name = code["code"], code["name"]
    item.status, item.confidence, item.zone = "auto", None, "high"
    when = decision.decided_at
    when = when if when.tzinfo else when.replace(tzinfo=UTC)  # SQLite keeps it without the zone
    item.summary = f"Код из решения человека: эта строка уже проверялась. {item.summary}"
    attributes = {k: v for k, v in item.attributes.items() if k != "review"}
    item.attributes = attributes | {"remembered": when.isoformat()}


def recalled(rows: list[parser.Item]) -> dict[str, Decision]:
    """Decisions of a person for the rows of a batch, by text_key; one query."""
    if not REMEMBER:
        return {}
    keys = {text_key(row.text) for row in rows}
    with SessionLocal() as session:
        found = session.scalars(select(Decision).where(Decision.key.in_(keys))).all()
    return {d.key: d for d in found}


def remember(session: Session, item: Item) -> None:
    """Keeps what a person gave the row; the last decision for a text wins."""
    if REMEMBER and item.ktru_code:
        decision = Decision(
            key=text_key(item.raw_text),
            job_id=item.job_id,
            ktru_code=item.ktru_code,
            ktru_name=item.ktru_name or "",
            decided_at=now(),
        )
        session.merge(decision)


def forget(session: Session, item: Item) -> None:
    """The decision this job made for the row text is taken back."""
    session.execute(
        delete(Decision).where(
            Decision.key == text_key(item.raw_text), Decision.job_id == item.job_id
        )
    )


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
    llm = extractor.JobExtractor([row.text for row in rows], cancelled=state.cancels.get(job_id))
    batches = [rows[start : start + BATCH] for start in range(0, len(rows), BATCH)]
    preparer = ThreadPoolExecutor(1, thread_name_prefix="prepare")
    try:
        prepared = [preparer.submit(prepare_batch, batch, llm, stages) for batch in batches]
        for n, batch in enumerate(batches):
            if cancelled(job_id):
                raise extractor.Cancelled
            started = time.monotonic()
            ready = prepared[n].result()
            tick(stages, "wait", started)
            process_batch(job_id, batch, n * BATCH, ready, include_inactive, stages)
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
    found = state.catalog.search(
        prepared["vectors"],
        prepared["queries"],
        include_inactive,
        units=[column or guessed for column, guessed in hints],
        column_units=[column for column, _ in hints],
        anchors=[row.text for row in batch],
    )
    tick(stages, "search", started)
    started = time.monotonic()
    known = recalled(batch)
    items = [
        build_item(job_id, row, attrs, cands, known.get(text_key(row.text)))
        for row, attrs, cands in zip(batch, attributes, found, strict=True)
    ]
    tick(stages, "confidence", started)
    started = time.monotonic()
    with SessionLocal() as session:
        session.add_all(items)
        session.commit()
    set_job(job_id, processed=start + len(batch), stage_seconds=dict(stages))
    tick(stages, "save", started)


def choose_codes(job_id: uuid.UUID, stages: dict, until: float) -> None:
    """Second opinion of a neural network for the rows the search was not sure about.

    An item changes only if it is still needs_review with the same code: edits made by the user
    while the job runs are never overwritten. The row stays for review: the confidence is the
    search's."""
    if not chooser.ENABLED or extractor.llm_state() != "on":
        return
    started = time.monotonic()
    with SessionLocal() as session:
        items = session.scalars(
            select(Item).where(Item.job_id == job_id, Item.status == "needs_review")
        ).all()
    rows = [
        {"id": item.id, "text": item.raw_text, "candidates": item.candidates}
        for item in items
        if len(item.candidates) > 1
    ]
    cancelled = state.cancels.get(job_id)
    picks = chooser.choose(rows, cancelled, until)
    with SessionLocal() as session:
        for item in items:
            code, provider = picks.get(item.id, (item.ktru_code, ""))
            if code == item.ktru_code:
                continue
            # No percent for this pick: the search's one was about another code, and the one a
            # lower candidate gets in its card says only that the search preferred another
            # (the right «Сканер» showed 0 % next to the search's wrong «3D-сканер»)
            chosen = next(c for c in item.candidates if c["code"] == code)
            summary = f"Код выбран нейросетью ({provider}) из 5 кандидатов поиска. {item.summary}"
            # The reason was about the search's first code; now it is about the choice
            first = item.candidates[0]
            review = (
                f"Нейросеть выбрала этот код вместо первого кандидата поиска {first['code']} "
                f"«{first['name']}»: проверьте выбор"
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
                    confidence=None,
                    zone="low",
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
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        path, include_inactive = upload_path(job), job.include_inactive
        session.execute(delete(Item).where(Item.job_id == job_id))  # rerun after a restart
        session.commit()
    state.cancels[job_id] = threading.Event()
    try:
        rows = parser.parse_file(path)
        tick(stages, "read", started)
        set_job(job_id, total=len(rows))
        process_items(job_id, rows, include_inactive, stages)
        choose_codes(job_id, stages, until=started + JOB_TIME_LIMIT)
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
    finish(job_id, status, error, stages)
    state.cancels.pop(job_id, None)
    path.unlink(missing_ok=True)


def worker_loop() -> None:
    while not state.stop.is_set():
        try:
            job_id = claim_job() if state.catalog is not None else None
            if job_id is not None:
                run_job(job_id)
                continue
        except Exception:  # the database restarts: the worker must survive and retry
            logger.exception("worker error, retrying")
        state.stop.wait(POLL_SECONDS)


def clean_old_jobs() -> int:
    """Deletes finished jobs older than KEEP_DAYS with their rows and files; the decisions of
    people stay, they are the memory for later files. Returns how many jobs went."""
    if KEEP_DAYS <= 0:
        return 0
    border = now() - timedelta(days=KEEP_DAYS)
    with SessionLocal() as session:
        old = session.scalars(
            select(Job).where(
                Job.status.in_(("done", "failed", "cancelled")), Job.created_at < border
            )
        ).all()
        ids = [job.id for job in old]
        if not ids:
            return 0
        session.execute(delete(Item).where(Item.job_id.in_(ids)))
        session.execute(delete(Job).where(Job.id.in_(ids), Job.status != "processing"))
        session.commit()
    for job in old:
        upload_path(job).unlink(missing_ok=True)
    logger.info("deleted %s jobs older than %s days", len(ids), KEEP_DAYS)
    return len(ids)


def cleaner_loop() -> None:
    while not state.stop.is_set():
        try:
            clean_old_jobs()
        except Exception:  # the database restarts: try again next time
            logger.exception("cleaning old jobs failed")
        state.stop.wait(CLEAN_EVERY)


def load_catalog() -> None:
    try:
        catalog = search.load_catalog()
        if catalog.vectors.shape[1] != embedder.DIM:
            raise ValueError(
                f"catalog vectors have {catalog.vectors.shape[1]} numbers, the Yandex embeddings "
                f"{embedder.DIM}: the file belongs to another model"
            )
        state.catalog = catalog
    except Exception as exc:
        logger.exception("catalog is not loaded")
        state.catalog_error = str(exc)[:300] or type(exc).__name__


def init_db() -> None:
    for attempt in range(30):
        try:
            Base.metadata.create_all(engine)
            break
        except Exception:
            if attempt == 29:
                raise
            logger.info("database is not ready, waiting")
            time.sleep(2)
    auth.AuthBase.metadata.create_all(engine)
    with SessionLocal() as session:
        session.execute(update(Job).where(Job.status == "processing").values(status="queued"))
        session.commit()
        admin = os.getenv("ADMIN_LOGIN", "admin")
        try:
            if auth.ensure_admin(session, admin, os.getenv("ADMIN_PASSWORD", "")):
                logger.info("the first admin %s is created from ADMIN_LOGIN", admin)
        except ValueError as exc:  # a short password: the site stays closed, the log says why
            logger.error("ADMIN_LOGIN/ADMIN_PASSWORD are not accepted: %s", exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    state.stop.clear()
    threading.Thread(target=load_catalog, daemon=True).start()
    for n in range(WORKERS):
        threading.Thread(target=worker_loop, name=f"worker-{n}", daemon=True).start()
    if KEEP_DAYS > 0:
        threading.Thread(target=cleaner_loop, name="cleaner", daemon=True).start()
    yield
    state.stop.set()


app = FastAPI(
    title="КТРУ-классификатор",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
    return JSONResponse({"detail": "Неверные параметры запроса"}, status_code=422)


@app.exception_handler(Exception)
async def internal_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("request failed", exc_info=exc)
    return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)


# --- accounts ------------------------------------------------------------------------------

throttle = auth.Throttle()


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Every API address except PUBLIC needs a session; the static site itself is nginx's."""
    path = request.url.path
    if not AUTH_REQUIRED or not path.startswith("/api/") or path in PUBLIC:
        return await call_next(request)
    user = await run_in_threadpool(session_user, request.cookies.get(COOKIE, ""))
    if user is None:
        return JSONResponse({"detail": "Войдите в систему"}, status_code=401)
    request.state.user = user
    return await call_next(request)


def session_user(token: str) -> auth.User | None:
    with SessionLocal() as session:
        return auth.user_by_token(session, token)


class LoginIn(BaseModel):
    login: str = Field(max_length=64)
    password: str = Field(max_length=200)


def user_out(user: auth.User) -> dict:
    return {"login": user.login, "is_admin": user.is_admin}


@app.post("/api/auth/login")
def log_in(body: LoginIn, request: Request, response: Response):
    key = (body.login.strip().lower(), client_address(request))
    if throttle.blocked(key):
        raise HTTPException(429, "Слишком много неудачных попыток: подождите минуту")
    with SessionLocal() as session:
        found = auth.log_in(session, body.login, body.password, SESSION_HOURS)
    if found is None:
        throttle.failed(key)
        raise HTTPException(401, "Неверный логин или пароль")
    throttle.passed(key)
    token, user = found
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(SESSION_HOURS * 3600),
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )
    return user_out(user)


def client_address(request: Request) -> str:
    """The address nginx saw: it appends it last to X-Forwarded-For; the backend port is local."""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    return forwarded or (request.client.host if request.client else "")


@app.post("/api/auth/logout", status_code=204)
def log_out(request: Request, response: Response):
    with SessionLocal() as session:
        auth.log_out(session, request.cookies.get(COOKIE, ""))
    response.delete_cookie(COOKIE, path="/")


@app.get("/api/auth/me")
def me(request: Request):
    user = getattr(request.state, "user", None)
    if user is None:  # AUTH_REQUIRED=0: nobody logged in, everything is open
        return {"login": "", "is_admin": False}
    return user_out(user)


def auth_state() -> str:
    """on: a login is needed; off: AUTH_REQUIRED=0; no_users: nobody can log in yet."""
    if not AUTH_REQUIRED:
        return "off"
    with SessionLocal() as session:
        return "on" if auth.has_users(session) else "no_users"


# --- endpoints -----------------------------------------------------------------------------


def to_dict(obj: Job | Item) -> dict:
    values = {column.name: getattr(obj, column.name) for column in obj.__table__.columns}
    return {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in values.items()}


def get_or_404(session: Session, model, key, what: str):
    try:
        obj = session.get(model, uuid.UUID(str(key)) if model is Job else key)
    except ValueError:
        obj = None
    if obj is None:
        raise HTTPException(404, f"{what} не найдено")
    return obj


def require_catalog() -> search.Catalog:
    if state.catalog is None:
        raise HTTPException(503, "Каталог КТРУ ещё загружается, подождите минуту")
    return state.catalog


@app.post("/api/jobs", status_code=202)
def create_job(
    file: Annotated[UploadFile, File()], include_inactive: Annotated[bool, Form()] = False
):
    # A plain def runs in the threadpool: reading, writing and the commit do not block the loop
    filename = Path(file.filename or "file").name[:200]
    data = file.file.read(parser.MAX_FILE_BYTES + 1)
    if len(data) > parser.MAX_FILE_BYTES:
        raise HTTPException(413, "Файл больше 10 МБ")
    if not data:
        raise HTTPException(400, "Файл пуст")
    try:
        parser.check_signature(Path(filename).suffix.lower(), data[:8])
    except parser.ParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    job = Job(id=uuid.uuid4(), filename=filename, include_inactive=include_inactive)
    path = upload_path(job)
    path.write_bytes(data)  # the name on disk is the uuid, not the user's name
    try:
        with SessionLocal() as session:
            session.add(job)
            session.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"job_id": str(job.id)}


JOB_ORDER = {
    "new": (Job.created_at.desc(),),
    "old": (Job.created_at.asc(),),
    "name": (func.lower(Job.filename).asc(), Job.created_at.desc()),
    "name_desc": (func.lower(Job.filename).desc(), Job.created_at.desc()),
}


@app.get("/api/jobs")
def list_jobs(
    response: Response,
    limit: int = Query(20, ge=1, le=1000),
    q: str = Query("", max_length=200),
    sort: Literal["new", "old", "name", "name_desc"] = "new",
):
    """Jobs by file name (case does not matter), newest first or as asked."""
    found = select(Job)
    if q.strip():
        found = found.where(Job.filename.ilike(like_pattern(q.strip()), escape="\\"))
    with SessionLocal() as session:
        # The page shows "N in the list", so the real number of matching jobs goes in a header
        response.headers["X-Total-Count"] = str(
            session.scalar(select(func.count()).select_from(found.subquery()))
        )
        jobs = session.scalars(found.order_by(*JOB_ORDER[sort]).limit(limit)).all()
        # One query for all the jobs: the page of the jobs shows how much of each is ready
        rows = session.execute(
            select(Item.job_id, Item.status, func.count())
            .where(Item.job_id.in_([job.id for job in jobs]))
            .group_by(Item.job_id, Item.status)
        )
        counts: dict = {job.id: {status: 0 for status in ITEM_STATUSES} for job in jobs}
        for job_id, status, count in rows:
            counts[job_id][status] = count
        return [to_dict(job) | {"counts": counts[job.id]} for job in jobs]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        rows = session.execute(
            select(Item.status, func.count()).where(Item.job_id == job.id).group_by(Item.status)
        )
        counts = {status: 0 for status in ITEM_STATUSES} | dict(rows.all())

        def flagged(key: str) -> int:
            return session.scalar(
                select(func.count())
                .select_from(Item)
                .where(Item.job_id == job.id, Item.attributes[key].as_string().is_not(None))
            )

        # fallback: the network did not give the attributes; words_only: no vectors
        return to_dict(job) | {
            "counts": counts,
            "fallback": flagged("fallback"),
            "words_only": flagged("words_only"),
        }


@app.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(job_id: str):
    """Removes the job with its rows and the uploaded file; a running job must be stopped first."""
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        if job.status == "cancelled" and job.id in state.cancels:
            # stopped by the user a moment ago: the worker finishes its batch first
            raise HTTPException(409, "Задание останавливается: удалите его через несколько секунд")
        if job.status == "processing" or job.id in state.cancels:
            raise HTTPException(409, "Задание ещё обрабатывается: сначала остановите его")
        session.execute(delete(Item).where(Item.job_id == job.id))
        session.execute(delete(Decision).where(Decision.job_id == job.id))
        # A worker may take a queued job right now: then nothing is deleted and rows come back
        gone = session.execute(
            delete(Job).where(Job.id == job.id, Job.status != "processing")
        ).rowcount
        if not gone:
            session.rollback()
            raise HTTPException(409, "Задание ещё обрабатывается: сначала остановите его")
        session.commit()
    upload_path(job).unlink(missing_ok=True)


def like_pattern(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


STATUS_ORDER = (
    "needs_review",
    "failed",
    "auto",
    "corrected",
    "accepted",
)  # what needs a look first


def order_by(sort: str, order: str) -> list:
    """ORDER BY of the list: by code, confidence or status; the row id keeps the ties stable."""
    if sort == "code":
        key = Item.ktru_code
    elif sort == "confidence":
        key = Item.confidence
    elif sort == "status":
        key = case({s: i for i, s in enumerate(STATUS_ORDER)}, value=Item.status, else_=9)
    else:
        return [Item.id]
    return [(key.desc() if order == "desc" else key.asc()).nulls_last(), Item.id]


@app.get("/api/jobs/{job_id}/items")
def list_items(
    job_id: str,
    status: str = "",
    q: str = Query("", max_length=200),
    sort: str = Query("", pattern="^(code|confidence|status)?$"),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    candidates: bool = True,  # the list of cards does not show them: ~10 KB less per row
):
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        query = select(Item).where(Item.job_id == job.id)
        if status:
            query = query.where(Item.status.in_(status.split(",")))
        if q.strip():
            pattern = like_pattern(q.strip())
            query = query.where(
                or_(
                    Item.raw_text.ilike(pattern, escape="\\"),
                    Item.ktru_code.ilike(pattern, escape="\\"),
                    Item.ktru_name.ilike(pattern, escape="\\"),
                )
            )
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        items = session.scalars(query.order_by(*order_by(sort, order)).limit(limit).offset(offset))
        rows = [to_dict(item) for item in items]
        if not candidates:
            for row in rows:
                del row["candidates"]
        return {"total": total, "items": rows}


ORIGINAL_FIELDS = ("ktru_code", "ktru_name", "confidence", "zone", "status", "summary")


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
    for field, value in original.items():
        setattr(item, field, value)
    item.attributes = attributes


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


@app.get("/api/items/{item_id}")
def get_item(item_id: int):
    """One position with its place in the job: the page of a code walks to the neighbours."""
    with SessionLocal() as session:
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


@app.post("/api/items/{item_id}/apply-same")
def apply_same(item_id: int):
    """The code a person gave this row goes to the identical rows of the job that wait."""
    with SessionLocal() as session:
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


@app.patch("/api/items/{item_id}")
def correct_item(item_id: int, body: Correction):
    # "10.12.10.000- 00000005" from a spreadsheet: spaces and line breaks are dropped
    code = require_catalog().get(re.sub(r"\s+", "", body.ktru_code))
    if code is None:
        raise HTTPException(400, "Такого кода нет в каталоге КТРУ")
    with SessionLocal() as session:
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
    name: str = Field("", max_length=100)
    value: str = Field(min_length=1, max_length=100)
    unit: str = Field("", max_length=30)


class AttributesIn(BaseModel):
    model_config = {"str_strip_whitespace": True}
    name: str = Field(min_length=1, max_length=300)
    brand: str = Field("", max_length=100)
    model: str = Field("", max_length=100)
    characteristics: list[CharacteristicIn] = Field(default_factory=list, max_length=30)


def manual_search_text(body: AttributesIn) -> str:
    """The query is built like the network's search_text: name, then «name: value unit»."""
    parts = [body.name]
    for c in body.characteristics:
        value = f"{c.value} {c.unit}".strip()
        parts.append(f"{c.name}: {value}" if c.name else value)
    return ". ".join(parts)


@app.put("/api/items/{item_id}/attributes")
def edit_attributes(item_id: int, body: AttributesIn):
    """A person fixes the attributes; the code is searched again by the fixed text."""
    catalog = require_catalog()
    query = manual_search_text(body)
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        include_inactive = session.get(Job, item.job_id).include_inactive
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


@app.post("/api/items/{item_id}/accept")
def accept_item(item_id: int):
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        if not item.ktru_code:
            raise HTTPException(400, "У позиции нет кода: выберите его вручную")
        remember_original(item)
        if item.status in ("needs_review", "failed"):
            remember(session, item)  # a person looked at a doubtful row and agreed
        item.status = "accepted"
        session.commit()
        return to_dict(item)


@app.post("/api/items/{item_id}/undo-code")
def undo_code(item_id: int):
    """Brings back the code the item had before the last manual change (also the old status)."""
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        attributes = dict(item.attributes or {})
        history = list(attributes.get("history", []))
        if not history:
            raise HTTPException(400, "Предыдущего кода нет")
        for field, value in history.pop().items():
            setattr(item, field, value)
        item.attributes = attributes | {"history": history}
        forget(session, item)
        if item.status == "corrected":
            remember(session, item)  # the code before was a person's too
        session.commit()
        return to_dict(item)


@app.post("/api/items/{item_id}/revert")
def revert_item(item_id: int):
    """Undoes accept or a manual code: the code the system proposed and its status come back."""
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        if item.status == "failed":
            raise HTTPException(400, "Позицию не удалось обработать: вернуть нечего")
        if item.status in ("accepted", "corrected"):
            restore_original(item)
            forget(session, item)
            session.commit()
        return to_dict(item)


@app.post("/api/jobs/{job_id}/accept")
def accept_job(job_id: str, min_confidence: float = Query(85.0, ge=0, le=100)):
    catalog = require_catalog()
    with SessionLocal() as session:
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
    ids: list[int] = Field(default_factory=list, max_length=1000)
    all_accepted: bool = False  # every accepted row of the job, not only the listed ones


@app.post("/api/jobs/{job_id}/revert")
def revert_items(job_id: str, body: RevertIn):
    """Returns accepted or corrected rows to review: the code the system proposed comes back."""
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        if body.all_accepted:
            conditions = [Item.status == "accepted"]
        else:
            conditions = [Item.status.in_(("accepted", "corrected")), Item.id.in_(body.ids)]
        items = session.scalars(select(Item).where(Item.job_id == job.id, *conditions)).all()
        for item in items:
            restore_original(item)
        session.commit()
        return {"reverted": len(items)}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Stops a queued or running job; finished rows stay for editing and export."""
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        was = job.status
        session.execute(
            update(Job)
            .where(Job.id == job.id, Job.status.in_(("queued", "processing")))
            .values(status="cancelled", finished_at=now())
        )
        session.commit()
        session.refresh(job)
    if was == "queued":
        upload_path(job).unlink(missing_ok=True)  # no worker will take it any more
    event = state.cancels.get(job.id)
    if event is not None:
        event.set()
    return to_dict(job)


MAX_LOOKUP = 500  # the catalog page lists many codes; a broad query is then worth narrowing


@app.get("/api/catalog/search")
def catalog_search(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=MAX_LOOKUP),
    include_inactive: bool = True,
):
    return require_catalog().lookup(q, limit, include_inactive=include_inactive)


@app.get("/api/health")
def health():
    catalog = "error" if state.catalog_error else "ready" if state.catalog else "loading"
    return {
        "status": "ok",
        "catalog": catalog,
        "codes": len(state.catalog.codes) if state.catalog else 0,
        "llm": extractor.llm_state(),
        "catalog_error": state.catalog_error,
        "auth": auth_state(),
        "embeddings": embedder.state(),
        "providers": [  # names and limits only, never keys
            {"name": p.name, "model": p.model, "parallel": p.parallel, "down": p.is_down()}
            for p in extractor.PROVIDERS
            if p.configured
        ],
    }


# --- export --------------------------------------------------------------------------------

STATUS_TEXT = {
    "auto": "Автоматически",
    "needs_review": "Нужна проверка",
    "corrected": "Исправлено",
    "accepted": "Принято",
    "failed": "Ошибка",
}
EXPORT_HEADER = [
    "Наименование (извлечено)",
    "Бренд",
    "Модель",
    "Характеристики (извлечено)",
    "Код КТРУ",
    "Наименование КТРУ",
    "Уверенность, %",
    "Статус кода",
    "Статус позиции",
]
CODE_STATUS_TEXT = {"ACTIVE": "действует", "TERMINATED": "отменён", "INACTIVE": "недействующий"}


def cell(value):
    """Formula injection guard: a text starting with = + - @ gets a leading apostrophe."""
    # Leading spaces or a line break do not hide a formula from a spreadsheet
    if isinstance(value, str) and (
        value[:1] in ("\t", "\r", "\n") or value.lstrip()[:1] in ("=", "+", "-", "@")
    ):
        return "'" + value
    return value


def export_rows(items: list[Item]) -> list[list]:
    columns = list(dict.fromkeys(k for i in items for k in (i.attributes or {}).get("columns", {})))
    rows = [["Строка", *columns, *EXPORT_HEADER]]
    for item in items:
        attrs = item.attributes or {}
        code = state.catalog.get(item.ktru_code) if state.catalog and item.ktru_code else None
        characteristics = "; ".join(
            f"{c.get('name')}: {c.get('value')} {c.get('unit')}".strip(": ")
            for c in attrs.get("characteristics", [])
        )
        row = [item.row_number, *(attrs.get("columns", {}).get(c, "") for c in columns)]
        row += [attrs.get(key, "") for key in ("name", "brand", "model")]
        row += [characteristics, item.ktru_code, item.ktru_name, item.confidence]
        row += [CODE_STATUS_TEXT.get(code["status"]) if code else "", STATUS_TEXT[item.status]]
        rows.append(row)
    return [[cell(value) for value in row] for row in rows]


def to_csv(rows: list[list]) -> bytes:
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=";").writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def to_xlsx(rows: list[list]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "КТРУ"
    for row in rows:
        sheet.append(row)
    for header_cell in sheet[1]:
        header_cell.font = Font(bold=True)
    sheet.freeze_panes = "B2"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@app.get("/api/jobs/{job_id}/export")
def export(job_id: str, format: str = Query("xlsx", pattern="^(xlsx|csv)$")):
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        if job.status not in ("done", "cancelled"):
            raise HTTPException(409, "Выгрузка доступна после окончания обработки")
        # The five candidates of a row (~10 KB) are not exported: they are not read from the base
        query = select(Item).options(defer(Item.candidates)).where(Item.job_id == job.id)
        items = session.scalars(query.order_by(Item.id)).all()
    rows = export_rows(list(items))
    if format == "csv":
        content, media = to_csv(rows), "text/csv; charset=utf-8"
    else:
        content = to_xlsx(rows)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    # the file is named after the uploaded one; the ASCII name is for old clients
    pretty = quote(f"{Path(job.filename).stem}_ktru.{format}")
    disposition = (
        f"attachment; filename=\"ktru_{str(job.id)[:8]}.{format}\"; filename*=UTF-8''{pretty}"
    )
    return Response(content, media_type=media, headers={"Content-Disposition": disposition})
