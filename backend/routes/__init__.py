"""API router assembly."""

from fastapi import APIRouter

from . import accounts, catalog, exports, items, jobs, privacy

router = APIRouter()
for group in (accounts, jobs, items, catalog, exports, privacy):
    router.include_router(group.router)
