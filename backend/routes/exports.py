"""Export specification rows to CSV and XLSX."""

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import (
    select,
)
from sqlalchemy.orm import defer

import database as db
import runtime as rt
from exporting import cell, to_csv, to_xlsx
from models import Item, Job
from routes.common import get_or_404

router = APIRouter()


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


def export_rows(items: list[Item]) -> list[list]:
    columns = list(dict.fromkeys(k for i in items for k in (i.attributes or {}).get("columns", {})))
    rows = [["Строка", *columns, *EXPORT_HEADER]]
    for item in items:
        attrs = item.attributes or {}
        code = rt.state.catalog.get(item.ktru_code) if rt.state.catalog and item.ktru_code else None
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


@router.get("/api/jobs/{job_id}/export")
def export(job_id: str, format: str = Query("xlsx", pattern="^(xlsx|csv)$")):
    with db.SessionLocal() as session:
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
