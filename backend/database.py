"""One engine and session factory."""

import os

from sqlalchemy import (
    create_engine,
)
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://ktru:ktru@db:5432/ktru")
sqlite = DATABASE_URL.startswith("sqlite")
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if sqlite else {},
)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
