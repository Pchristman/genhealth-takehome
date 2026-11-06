"""Serve the built frontend, when there is one.

This is the 404 handler, not a catch-all route. A route at `/{path:path}` would match every path:
`/v1/orders/` would stop redirecting and `POST /v1/nothing` would become a 405. This way the API
routes behave the same with or without a build, and the frontend only sees what they left over.
"""

from pathlib import Path

from fastapi import Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException
from starlette.responses import Response

from app.config import settings

API_ROOTS = frozenset({"auth", "v1", "docs", "redoc", "openapi.json", "health"})
REVALIDATE = "no-cache"  # index.html names the bundles, so it must never outlive a deploy
FOREVER = "public, max-age=31536000, immutable"  # bundle names change whenever their content does


def _names_a_file(relative: str) -> bool:
    """Whether this asks for a file. A browser would run index.html as that file, so it 404s."""
    parts = relative.lower().split("/")
    return parts[0] == "assets" or "." in parts[-1]


def _static_file(relative: str) -> Path | None:
    """The file to serve: a file in the build, index.html for an app path, or None to 404."""
    root = settings.static_dir.resolve()
    index = root / "index.html"
    if not index.is_file():
        return None
    try:
        target = (root / relative).resolve()
        if target.is_relative_to(root) and target.is_file():
            return target
    except (OSError, ValueError):  # a NUL byte or an over-long name is simply not one of our files
        pass
    return None if _names_a_file(relative) else index


def _cache_control(file: Path) -> str:
    return FOREVER if file.is_relative_to(settings.static_dir.resolve() / "assets") else REVALIDATE


async def serve_or_404(request: Request, exc: HTTPException) -> Response:
    relative = request.scope["path"].lstrip("/")
    if request.method in ("GET", "HEAD") and relative.split("/")[0].lower() not in API_ROOTS:
        file = _static_file(relative)
        if file is not None:
            return FileResponse(file, headers={"Cache-Control": _cache_control(file)})
    return await http_exception_handler(request, exc)
