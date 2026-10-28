"""Catalog lookup and service health endpoints."""

from fastapi import APIRouter, Query

import embedder
import extractor
import runtime as rt
from routes.accounts import auth_state
from routes.common import require_catalog
from routes.items import MAX_LOOKUP

router = APIRouter()


@router.get("/api/catalog/search")
def catalog_search(
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=MAX_LOOKUP),
    include_inactive: bool = True,
):
    return require_catalog().lookup(q, limit, include_inactive=include_inactive)


@router.get("/api/health")
def health():
    catalog = "error" if rt.state.catalog_error else "ready" if rt.state.catalog else "loading"
    return {
        "status": "ok",
        "catalog": catalog,
        "codes": len(rt.state.catalog.codes) if rt.state.catalog else 0,
        "llm": extractor.llm_state(),
        "catalog_error": rt.state.catalog_error,
        "auth": auth_state(),
        "embeddings": embedder.state(),
        "queue": "celery",
        "vector_search": "pgvector"
        if getattr(rt.state.catalog, "vector_store", None)
        else "offline",
        "providers": [  # names and limits only, never keys
            {"name": p.name, "model": p.model, "parallel": p.parallel, "down": p.is_down()}
            for p in extractor.PROVIDERS
            if p.configured
        ],
    }
