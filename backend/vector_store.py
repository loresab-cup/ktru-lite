"""Versioned catalog vectors and exact nearest-neighbor queries in PostgreSQL."""

import hashlib
import json

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Boolean, Column, Integer, MetaData, String, Table, select, text

import database as db

metadata = MetaData()
vectors = Table(
    "catalog_vectors",
    metadata,
    Column("revision", String(64), primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("code", String(32), nullable=False),
    Column("active", Boolean, nullable=False),
    Column("embedding", Vector(256).with_variant(JSON(), "sqlite"), nullable=False),
)


class VectorStore:
    def __init__(self, catalog):
        self.catalog = catalog
        digest = hashlib.sha256(catalog.vectors.tobytes())
        digest.update(json.dumps([catalog.codes, catalog.statuses]).encode())
        self.revision = digest.hexdigest()

    def sync(self):
        with db.engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(84712351)"))
            count = connection.scalar(
                text("SELECT count(*) FROM catalog_vectors WHERE revision=:r"), {"r": self.revision}
            )
            if count == len(self.catalog.codes):
                return
            connection.execute(vectors.delete().where(vectors.c.revision == self.revision))
            raw = connection.connection.driver_connection
            with raw.cursor().copy(
                "COPY catalog_vectors (revision, position, code, active, embedding) FROM STDIN"
            ) as copy:
                for i, (code, row) in enumerate(
                    zip(self.catalog.codes, self.catalog.vectors, strict=True)
                ):
                    embedding = "[" + ",".join(repr(float(v)) for v in row) + "]"
                    copy.write_row(
                        (self.revision, i, code, bool(self.catalog.active[i]), embedding)
                    )

    def nearest(self, queries, include_inactive, limit):
        result = []
        with db.engine.connect() as connection:
            for query in queries:
                distance = vectors.c.embedding.max_inner_product(query)
                statement = select(vectors.c.position).where(vectors.c.revision == self.revision)
                if not include_inactive:
                    statement = statement.where(vectors.c.active.is_(True))
                # Exact scan: no HNSW/IVFFlat index and no approximate recall loss.
                indices = list(
                    connection.scalars(
                        statement.order_by(distance, vectors.c.position).limit(limit)
                    )
                )
                scores = self.catalog.vectors[indices] @ query
                result.append(
                    [(int(i), float(score)) for i, score in zip(indices, scores, strict=True)]
                )
        return result
