"""Application assembly and lifecycle."""

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import access
import routes
import runtime as rt
from workers import init_db, load_catalog_when_available

logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    rt.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    stop = threading.Event()
    threading.Thread(target=load_catalog_when_available, args=(stop,), daemon=True).start()
    try:
        yield
    finally:
        stop.set()


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


@app.middleware("http")
async def require_login(request: Request, call_next):
    """Every API address except rt.PUBLIC needs a session; the static site itself is nginx's."""
    path = request.url.path
    if not rt.AUTH_REQUIRED or not path.startswith("/api/") or path in rt.PUBLIC:
        return await call_next(request)
    user = await run_in_threadpool(routes.accounts.session_user, request.cookies.get(rt.COOKIE, ""))
    if user is None:
        return JSONResponse({"detail": "Войдите в систему"}, status_code=401)
    request.state.user = user
    scope = access.current_user.set(user.id)
    try:
        return await call_next(request)
    finally:
        access.current_user.reset(scope)


app.include_router(routes.router)
