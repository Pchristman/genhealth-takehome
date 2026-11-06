from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import pytest

from app.config import Settings, settings

INDEX = "<!doctype html><title>Order Intake</title>"
SCRIPT = "console.info('bundle')"
SECRET = "TOP-SECRET-OUTSIDE-STATIC"
JSON_404 = {"detail": "Not Found"}


@pytest.fixture
def built(tmp_path, monkeypatch) -> Path:
    """A built frontend in a temp dir, with a secret beside it that it must never serve."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(INDEX)
    (static / "assets" / "app.js").write_text(SCRIPT)
    (tmp_path / "secret.txt").write_text(SECRET)
    (tmp_path / "static-private").mkdir()
    (tmp_path / "static-private" / "secret.txt").write_text(SECRET)
    monkeypatch.setattr(settings, "static_dir", static)
    return static


def test_spa_served_at_root(client, built):
    response = client.get("/")

    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")


def test_spa_serves_static_assets(client, built):
    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert response.text == SCRIPT


@pytest.mark.parametrize("path", ["/orders", "/orders/123/detail", "/login"])
def test_spa_falls_back_to_index_for_other_get_paths(client, built, path):
    response = client.get(path)

    assert response.status_code == 200
    assert response.text == INDEX


@pytest.mark.parametrize(
    "path",
    [
        "/v1/nothing",
        "/v1",
        "/v1/",
        "/v1/orders/not-a-route/at/all",
        "/auth/nothing",
        "/auth",
        "/docs/nothing",
        "/openapi.json/nothing",
        "/health/nothing",
    ],
)
def test_spa_fallback_does_not_shadow_api(client, built, path):
    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == JSON_404
    assert response.headers["content-type"].startswith("application/json")


def test_spa_fallback_does_not_hide_a_routes_own_404(client, built, auth_headers):
    response = client.get(f"/v1/orders/{uuid4()}", headers=auth_headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "Order not found."}


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("/health", "application/json"),
        ("/openapi.json", "application/json"),
        ("/docs", "text/html"),
    ],
)
def test_spa_leaves_public_api_paths_alone(client, built, path, kind):
    response = client.get(path)

    assert response.status_code == 200
    assert response.text != INDEX
    assert response.headers["content-type"].startswith(kind)


def test_spa_does_not_change_wrong_method_or_trailing_slash(client, built, auth_headers):
    headers = auth_headers()

    assert client.put("/v1/orders", headers=headers).status_code == 405
    assert client.post("/nothing").status_code == 404
    redirected = client.get("/v1/orders/", headers=headers, follow_redirects=False)
    assert redirected.status_code == 307


@pytest.mark.parametrize(
    "path",
    [
        "/%2e%2e/secret.txt",
        "/..%2fsecret.txt",
        "/%2e%2e%2fsecret.txt",
        "/..%5csecret.txt",
        "/assets/..%2f..%2fsecret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/..%2fstatic-private%2fsecret.txt",
    ],
)
def test_spa_refuses_traversal_out_of_the_static_dir(client, built, path):
    response = client.get(path)

    assert SECRET not in response.text
    assert response.status_code == 200
    assert response.text == INDEX


def test_spa_refuses_an_absolute_path(client, built):
    target = (built.parent / "secret.txt").as_posix()

    response = client.get("/" + quote(target, safe=""))

    assert SECRET not in response.text
    assert response.text == INDEX


@pytest.mark.parametrize("path", ["/bad%00name", "/assets/app.js%00.html", "/" + "a" * 5000])
def test_spa_survives_a_path_the_filesystem_rejects(client, built, path):
    response = client.get(path)

    assert response.status_code == 200
    assert response.text == INDEX


def test_spa_control_the_secret_is_reachable_when_inside(client, built):
    # Proves the traversal tests fail for the right reason: this file is served when it is inside.
    (built / "secret.txt").write_text(SECRET)

    assert client.get("/secret.txt").text == SECRET


@pytest.mark.parametrize("path", ["/", "/orders", "/v1/nothing"])
def test_no_build_dir_means_api_only(client, tmp_path, monkeypatch, path):
    monkeypatch.setattr(settings, "static_dir", tmp_path / "missing")

    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == JSON_404


def test_static_dir_defaults_to_backend_static(monkeypatch):
    monkeypatch.delenv("STATIC_DIR", raising=False)
    backend = Path(__file__).resolve().parents[1]

    assert Settings(_env_file=None).static_dir == backend / "static"


def test_static_dir_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("STATIC_DIR", str(tmp_path))

    assert Settings(_env_file=None).static_dir == tmp_path
