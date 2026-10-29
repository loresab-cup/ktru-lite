"""Process configuration and shared state."""

import os
import threading
import uuid
from pathlib import Path

import search

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://ktru:ktru@db:5432/ktru")
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "/data/uploads"))
# Finished jobs older than this are deleted once an hour; 0 keeps them (the default)
KEEP_DAYS = float(os.getenv("JOBS_KEEP_DAYS", "0"))
JOB_TIME_LIMIT = 270.0  # seconds: 1000 rows must be done within 5 minutes, with a margin
BATCH = 20  # small batches: finished rows reach the table while the rest are processed
ITEM_STATUSES = ("auto", "needs_review", "corrected", "accepted", "failed")
# A code a person gave a row goes to the same row of later files (REMEMBER_DECISIONS=0 - off)
REMEMBER = os.getenv("REMEMBER_DECISIONS", "1") != "0"
# Accounts: every /api/ address except PUBLIC needs a login (AUTH_REQUIRED=0 - only for development)
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "1") != "0"
SESSION_HOURS = float(os.getenv("SESSION_HOURS", "12"))
# A guest's session (its cookie itself ends with the browser); then the worker removes its files
GUEST_HOURS = float(os.getenv("GUEST_HOURS", "12"))
COOKIE = "ktru_session"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"  # 1 behind HTTPS
PUBLIC = {
    "/api/health",
    "/api/legal",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/guest",
    "/api/auth/logout",
    "/api/docs",
    "/api/openapi.json",
}


class State:
    catalog: search.Catalog | None = None
    catalog_error: str = ""  # why the catalog is not loaded, for /api/health

    def __init__(self) -> None:
        self.cancels: dict[uuid.UUID, threading.Event] = {}  # running jobs -> "stop" flag


state = State()
