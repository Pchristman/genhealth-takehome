"""Refuse oversized request bodies, for everyone, before anything parses them.

FastAPI reads the whole body before it checks the token, so without this any stranger could make
the server hold an arbitrary amount in memory. This is pure ASGI on purpose: it passes each chunk
straight on and holds none of them.
"""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

BODY_LIMIT = 1_000_000
MULTIPART_OVERHEAD = 1_000_000  # the upload route enforces the exact size on the file itself
UPLOAD_ROUTE = ("POST", "/v1/orders/upload")
TOO_LARGE = "Request body too large."


def _limit_for(scope: Scope) -> int:
    if (scope["method"], scope["path"]) == UPLOAD_ROUTE:
        return settings.upload_max_bytes + MULTIPART_OVERHEAD
    return BODY_LIMIT


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None  # not a number to trust; the bytes still get counted
    return None


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = _limit_for(scope)
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            response = JSONResponse({"detail": TOO_LARGE}, status_code=413)
            await response(scope, receive, send)
            return

        seen = 0

        async def counted_receive() -> Message:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    # FastAPI re-raises this type from a body read, and its handler answers.
                    # Any other exception there would become a 400.
                    raise HTTPException(413, detail=TOO_LARGE)
            return message

        await self.app(scope, counted_receive, send)
