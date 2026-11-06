import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import auth
from app.config import settings
from app.db import init_db
from app.logging_config import configure_logging, request_id_var

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(settings.log_level)
    init_db()
    yield


app = FastAPI(title="Order Intake API", lifespan=lifespan)
app.include_router(auth.router)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default echoes the submitted input, which can be a password or a patient field.
    # Custom value errors can quote the input in their message, so those get a fixed one.
    details = [
        {
            "loc": list(error["loc"]),
            "type": error["type"],
            "msg": "Invalid value." if error["type"] == "value_error" else error["msg"],
        }
        for error in exc.errors()
    ]
    return JSONResponse({"detail": details}, status_code=422)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = str(uuid4())  # generated here; a client-sent value would be log injection
    token = request_id_var.set(request_id)
    start = time.perf_counter()
    status_code = 500
    try:
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_error")
            response = JSONResponse({"detail": "Internal server error."}, status_code=500)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        route = request.scope.get("route")
        log.info(
            "request",
            extra={
                "method": request.method,
                # the template, not the path: /v1/orders/{order_id} rather than a real id
                "route": getattr(route, "path", "unmatched"),
                "status_code": status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        )
        request_id_var.reset(token)


@app.get("/health", tags=["ops"])
def health() -> dict[str, str]:
    return {"status": "ok"}
