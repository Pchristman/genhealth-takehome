import asyncio
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.body_limit import BodyLimitMiddleware
from app.config import settings
from app.main import app

LIMIT = 1_000_000
TOO_LARGE = {"detail": "Request body too large."}
FORM = {"Content-Type": "application/x-www-form-urlencoded"}
JSON = {"Content-Type": "application/json"}
ROUTES = [
    ("POST", "/v1/orders", JSON),
    ("PATCH", f"/v1/orders/{uuid4()}", JSON),
    ("POST", "/auth/register", JSON),
    ("POST", "/auth/token", FORM),
]


@pytest.mark.parametrize(("method", "path", "headers"), ROUTES)
def test_declared_length_over_the_limit_is_refused_without_auth(client, method, path, headers):
    body = b"x" * (LIMIT + 1)
    response = client.request(method, path, content=body, headers=headers)
    assert response.status_code == 413
    assert response.json() == TOO_LARGE
    assert response.headers["X-Request-ID"]


@pytest.mark.parametrize(("method", "path", "headers"), ROUTES)
def test_chunked_body_over_the_limit_is_refused_without_auth(client, method, path, headers):
    chunks = iter([b"x" * LIMIT, b"x"])
    response = client.request(method, path, content=chunks, headers=headers)
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert response.json() == TOO_LARGE
    assert response.headers["X-Request-ID"]


def test_declared_and_chunked_limits_apply_with_auth_too(client, auth_headers):
    headers = {**auth_headers(), **JSON}
    body = b"x" * (LIMIT + 1)
    assert client.post("/v1/orders", content=body, headers=headers).status_code == 413
    assert client.post("/v1/orders", content=iter([body]), headers=headers).status_code == 413


def test_a_body_exactly_at_the_limit_gets_through(client, auth_headers):
    headers = {**auth_headers(), **JSON}
    body = b"x" * LIMIT
    assert client.post("/v1/orders", content=body, headers=headers).status_code == 422
    assert client.post("/v1/orders", content=iter([body]), headers=headers).status_code == 422


def test_upload_route_declared_limit_follows_the_setting(client, monkeypatch):
    monkeypatch.setattr(settings, "upload_max_bytes", 2_000_000)
    limit = 3_000_000
    fits = client.post("/v1/orders/upload", content=b"x" * limit)
    assert fits.status_code != 413
    over = client.post("/v1/orders/upload", content=b"x" * (limit + 1))
    assert over.status_code == 413
    assert over.json() == TOO_LARGE


def test_upload_limit_is_only_for_post(client):
    assert client.patch("/v1/orders/upload", content=b"x" * (LIMIT + 1)).status_code == 413


def test_upload_limit_does_not_reach_other_paths(client, monkeypatch):
    monkeypatch.setattr(settings, "upload_max_bytes", 2_000_000)
    assert client.post("/v1/orders", content=b"x" * (LIMIT + 1)).status_code == 413


@pytest.fixture
def probe():
    """An app with the middleware and routes that read the body, standing in for the upload."""
    scratch = FastAPI()
    scratch.add_middleware(BodyLimitMiddleware)

    class Body(BaseModel):
        name: str = ""

    @scratch.post("/v1/orders/upload")
    async def read_all(request: Request) -> dict[str, int]:
        return {"bytes": len(await request.body())}

    @scratch.post("/parsed")
    def parsed(body: Body) -> dict[str, str]:
        return {"name": body.name}

    @scratch.post("/stream")
    async def stream(request: Request) -> dict[str, list[int]]:
        return {"chunks": [len(chunk) async for chunk in request.stream() if chunk]}

    return scratch


def test_chunked_upload_counts_against_the_upload_limit(probe, monkeypatch):
    monkeypatch.setattr(settings, "upload_max_bytes", 2_000_000)
    client = TestClient(probe)
    fits = client.post("/v1/orders/upload", content=iter([b"x" * 3_000_000]))
    assert fits.json() == {"bytes": 3_000_000}
    over = client.post("/v1/orders/upload", content=iter([b"x" * 3_000_001]))
    assert over.status_code == 413
    assert over.json() == TOO_LARGE


def drive(asgi_app, path: str, chunks: list[bytes], headers=()) -> tuple[list[dict], int]:
    """Feed the app one ASGI message per chunk. Returns what it sent and how many it read."""
    remaining = list(chunks)
    sent = []
    reads = 0

    async def receive():
        nonlocal reads
        reads += 1
        return {
            "type": "http.request",
            "body": remaining.pop(0),
            "more_body": bool(remaining),
        }

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json"), *headers],
        "client": ("testclient", 1),
        "server": ("testserver", 80),
    }
    asyncio.run(asgi_app(scope, receive, send))
    return sent, reads


@pytest.mark.parametrize("path", ["/parsed", "/stream"])
def test_reading_stops_at_the_chunk_that_crosses_the_limit(probe, path):
    chunk = b"x" * 400_000
    sent, reads = drive(probe, path, [chunk] * 5)
    assert sent[0]["status"] == 413
    assert reads == 3


def test_real_app_stops_reading_at_the_chunk_that_crosses_the_limit():
    chunk = b"x" * 400_000
    sent, reads = drive(app, "/v1/orders", [chunk] * 5)
    assert sent[0]["status"] == 413
    assert reads == 3


def test_declared_length_over_the_limit_reads_nothing(probe):
    sent, reads = drive(probe, "/parsed", [b"x"], [(b"content-length", b"1000001")])
    assert sent[0]["status"] == 413
    assert reads == 0


def test_unreadable_content_length_is_still_counted(probe):
    sent, _ = drive(probe, "/parsed", [b"x" * (LIMIT + 1)], [(b"content-length", b"nonsense")])
    assert sent[0]["status"] == 413


def test_chunks_reach_the_app_as_they_arrive(probe):
    # A buffering middleware would hand the route one joined chunk.
    sent, _ = drive(probe, "/stream", [b"x" * 10, b"y" * 20, b"z" * 30])
    assert sent[0]["status"] == 200
    assert sent[1]["body"] == b'{"chunks":[10,20,30]}'
