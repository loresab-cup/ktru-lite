"""HTTP API, the job queue and the export.

Queue = the jobs table: WORKERS background threads take a queued job with
SELECT ... FOR UPDATE SKIP LOCKED and process it in batches of 100, updating `processed`.
Pipeline of a batch: extractor (LLM or rules) -> embedder (bge-m3) -> search -> confidence.
"""

import csv
import io
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    String,
    Text,
    Uuid,
    create_engine,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

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
BATCH = 100
POLL_SECONDS = 1.0
ITEM_STATUSES = ("auto", "needs_review", "corrected", "accepted", "failed")


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


sqlite = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if sqlite else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class State:
    catalog: search.Catalog | None = None
    catalog_error: bool = False
    stop = threading.Event()


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
    set_job(job_id, status=status, error=error, finished_at=now(), stage_seconds=dict(stages))


def tick(stages: dict, stage: str, started: float) -> None:
    stages[stage] = round(stages.get(stage, 0.0) + time.monotonic() - started, 2)


def build_item(job_id: uuid.UUID, row: parser.Item, attributes: dict, found: list) -> Item:
    item = Item(
        job_id=job_id,
        row_number=row.row_number,
        raw_text=row.text,
        candidates=found,
        attributes={**attributes, "columns": row.columns},
    )
    try:
        verdict = confidence.assess(found, is_product=bool(attributes.get("name")))
        best = found[0] if found else {}
        item.ktru_code, item.ktru_name = best.get("code"), best.get("name")
        item.confidence, item.zone = verdict["confidence"], verdict["zone"]
        item.status, item.summary = verdict["status"], verdict["summary"]
    except Exception as exc:  # one broken row must not stop the job
        logger.exception("item failed")
        item.status, item.summary = "failed", f"Ошибка обработки позиции: {type(exc).__name__}"
    return item


def process_items(
    job_id: uuid.UUID, rows: list[parser.Item], include_inactive: bool, stages: dict
) -> None:
    llm = extractor.JobExtractor([row.text for row in rows])
    for start in range(0, len(rows), BATCH):
        batch = rows[start : start + BATCH]
        started = time.monotonic()
        attributes = [llm.get(row.text) for row in batch]
        tick(stages, "extract", started)
        started = time.monotonic()
        queries = [
            f"{a['search_text']}. {row.text}" for a, row in zip(attributes, batch, strict=True)
        ]
        found = state.catalog.search(embedder.embed(queries), queries, include_inactive)
        tick(stages, "search", started)
        started = time.monotonic()
        items = [build_item(job_id, *args) for args in zip(batch, attributes, found, strict=True)]
        tick(stages, "confidence", started)
        with SessionLocal() as session:
            session.add_all(items)
            session.commit()
        set_job(job_id, processed=start + len(batch), stage_seconds=dict(stages))


def run_job(job_id: uuid.UUID) -> None:
    stages: dict = {}
    started = time.monotonic()
    with SessionLocal() as session:
        job = session.get(Job, job_id)
        path, include_inactive = upload_path(job), job.include_inactive
        session.execute(delete(Item).where(Item.job_id == job_id))  # rerun after a restart
        session.commit()
    try:
        rows = parser.parse_file(path)
        tick(stages, "read", started)
        set_job(job_id, total=len(rows))
        process_items(job_id, rows, include_inactive, stages)
        tick(stages, "total", started)
        finish(job_id, "done", None, stages)
    except parser.ParseError as exc:
        finish(job_id, "failed", str(exc), stages)
    except FileNotFoundError:
        finish(job_id, "failed", "Файл задания не найден: загрузите его заново", stages)
    except Exception:
        logger.exception("job %s failed", job_id)
        finish(job_id, "failed", "Не удалось обработать файл, попробуйте ещё раз", stages)
    finally:
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


def load_catalog() -> None:
    try:
        state.catalog = search.load_catalog()
    except Exception:
        logger.exception("catalog is not loaded")
        state.catalog_error = True


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
    with SessionLocal() as session:
        session.execute(update(Job).where(Job.status == "processing").values(status="queued"))
        session.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    state.stop.clear()
    threading.Thread(target=load_catalog, daemon=True).start()
    for n in range(WORKERS):
        threading.Thread(target=worker_loop, name=f"worker-{n}", daemon=True).start()
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
async def create_job(
    file: Annotated[UploadFile, File()], include_inactive: Annotated[bool, Form()] = False
):
    filename = Path(file.filename or "file").name[:200]
    data = await file.read(parser.MAX_FILE_BYTES + 1)
    if len(data) > parser.MAX_FILE_BYTES:
        raise HTTPException(413, "Файл больше 10 МБ")
    if not data:
        raise HTTPException(400, "Файл пуст")
    try:
        parser.check_signature(Path(filename).suffix.lower(), data[:8])
    except parser.ParseError as exc:
        raise HTTPException(400, str(exc)) from exc
    with SessionLocal() as session:
        job = Job(id=uuid.uuid4(), filename=filename, include_inactive=include_inactive)
        upload_path(job).write_bytes(data)  # the name on disk is the uuid, not the user's name
        session.add(job)
        session.commit()
    return {"job_id": str(job.id)}


@app.get("/api/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100)):
    with SessionLocal() as session:
        jobs = session.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit))
        return [to_dict(job) for job in jobs]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with SessionLocal() as session:
        job = get_or_404(session, Job, job_id, "Задание")
        rows = session.execute(
            select(Item.status, func.count()).where(Item.job_id == job.id).group_by(Item.status)
        )
        counts = {status: 0 for status in ITEM_STATUSES} | dict(rows.all())
        return to_dict(job) | {"counts": counts}


def like_pattern(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@app.get("/api/jobs/{job_id}/items")
def list_items(
    job_id: str,
    status: str = "",
    q: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
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
        items = session.scalars(query.order_by(Item.id).limit(limit).offset(offset))
        return {"total": total, "items": [to_dict(item) for item in items]}


class Correction(BaseModel):
    ktru_code: str = Field(min_length=1, max_length=32)


@app.patch("/api/items/{item_id}")
def correct_item(item_id: int, body: Correction):
    code = require_catalog().get(body.ktru_code)
    if code is None:
        raise HTTPException(400, "Такого кода нет в каталоге КТРУ")
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        item.ktru_code, item.ktru_name, item.status = code["code"], code["name"], "corrected"
        item.confidence, item.zone, item.summary = 100.0, "high", "Код выбран вручную"
        session.commit()
        return to_dict(item)


@app.post("/api/items/{item_id}/accept")
def accept_item(item_id: int):
    with SessionLocal() as session:
        item = get_or_404(session, Item, item_id, "Позиция")
        if not item.ktru_code:
            raise HTTPException(400, "У позиции нет кода: выберите его вручную")
        item.status = "accepted"
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
            if code and code["status"] == "ACTIVE":
                item.status, accepted = "accepted", accepted + 1
        session.commit()
        return {"accepted": accepted}


@app.get("/api/catalog/search")
def catalog_search(q: str = Query(..., min_length=1, max_length=100)):
    return require_catalog().lookup(q)


@app.get("/api/health")
def health():
    catalog = "error" if state.catalog_error else "ready" if state.catalog else "loading"
    return {
        "status": "ok",
        "catalog": catalog,
        "codes": len(state.catalog.codes) if state.catalog else 0,
        "llm": bool(extractor.API_KEY),
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
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
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
        items = session.scalars(select(Item).where(Item.job_id == job.id).order_by(Item.id)).all()
    rows = export_rows(list(items))
    if format == "csv":
        content, media = to_csv(rows), "text/csv; charset=utf-8"
    else:
        content = to_xlsx(rows)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    disposition = f'attachment; filename="ktru_{str(job.id)[:8]}.{format}"'
    return Response(content, media_type=media, headers={"Content-Disposition": disposition})
