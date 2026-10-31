"""Jury startup data: admin/admin and a validated snapshot of an internal benchmark.

Loaded automatically at Docker startup with JURY_DATA=1; no external API calls.
The optional command python demo.py --create imports the same snapshot manually.
"""

import argparse
import base64
import gzip
import json
import logging
import os
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

import auth
import database as db
import runtime as rt
from models import Item, Job, now

SNAPSHOT = Path(__file__).with_name("demo_data") / "demo-1000.json.gz"
DEMO_ID = uuid.uuid5(uuid.NAMESPACE_URL, "ktru-lite/jury-demo/1000-991/v1")
DEMO_FILENAME = "Спецификация_1000_строк.xlsx"
logger = logging.getLogger(__name__)


def load_snapshot(path: Path = SNAPSHOT) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as source:
        data = json.load(source)
    rows = data["items"]
    reference = data["reference_codes"]
    numbers = [row["row_number"] for row in rows]
    exact = sum(row["ktru_code"] == reference[str(row["row_number"])] for row in rows)
    if (
        len(rows) != 1000
        or len(reference) != 1000
        or len(set(numbers)) != 1000
        or data["total"] != 1000
        or data["exact"] != exact
        or exact != 991
        or any(row["status"] not in ("auto", "needs_review") for row in rows)
    ):
        raise ValueError("Демонстрационный снимок не соответствует проверенному прогону 991/1000")
    return data


def seed_demo(session, data: dict, upload_dir: Path) -> tuple[uuid.UUID, bool]:
    """Create the demo once; never reset an existing account or edited specification."""
    user = session.scalar(select(auth.User).where(auth.User.login == "admin"))
    if user and (
        user.is_guest or not user.is_admin or not auth.verify_password("admin", user.password_hash)
    ):
        raise ValueError("Аккаунт admin уже существует с другими настройками: он не изменён")
    if user is None:
        # Only the jury startup account permits the requested five-letter password.
        # Registration and normal administrator creation retain their password policy.
        user = auth.User(login="admin", password_hash=auth.hash_password("admin"), is_admin=True)
        session.add(user)
        session.flush()
    existing = session.get(Job, DEMO_ID)
    if existing is not None:
        if existing.owner_id != user.id:
            raise ValueError("Демонстрационное задание принадлежит другому владельцу")
        if existing.filename == "Демо_1000_строк_991_из_1000.xlsx":
            existing.filename = DEMO_FILENAME
            session.commit()
        return existing.id, False
    finished = now()
    job = Job(
        id=DEMO_ID,
        owner_id=user.id,
        filename=DEMO_FILENAME,
        status="done",
        total=1000,
        processed=1000,
        created_at=finished,
        started_at=finished - timedelta(seconds=data["stage_seconds"]["total"]),
        finished_at=finished,
        stage_seconds=data["stage_seconds"],
    )
    session.add(job)
    session.flush()
    session.add_all(Item(job_id=job.id, **row) for row in data["items"])
    upload_dir.mkdir(parents=True, exist_ok=True)
    target = upload_dir / f"{job.id}.xlsx"
    target.write_bytes(base64.b64decode(data["input_base64"], validate=True))
    session.commit()
    return job.id, True


def seed_on_startup() -> None:
    """Compose enables jury data by default; existing accounts are never overwritten."""
    if os.getenv("JURY_DATA", "0") != "1":
        return
    data = load_snapshot()
    with db.SessionLocal() as session:
        try:
            job_id, created = seed_demo(session, data, rt.UPLOAD_DIR)
        except ValueError as exc:
            session.rollback()
            logger.warning("jury data skipped: %s", exc)
            return
    logger.info("jury data %s: %s", "created" if created else "already present", job_id)


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--create", action="store_true", required=True, help="create local jury demo")
    args.parse_args()
    data = load_snapshot()
    with db.SessionLocal() as session:
        job_id, created = seed_demo(session, data, rt.UPLOAD_DIR)
    print(f"Демо {'создано' if created else 'уже есть'}: admin / admin, задание {job_id}")
    print("Сохранённый внутренний прогон: 991/1000. Это не независимая оценка новых закупок.")
    print("Учётная запись с известным паролем предназначена только для локальной демонстрации.")


if __name__ == "__main__":
    main()
