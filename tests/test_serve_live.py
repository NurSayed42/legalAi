"""Live view এর নিরাপত্তা: UI আর API file অপরিবর্তিত, production port নয়, শুধু এই computer। api_v3 load করে না, তাই দ্রুত।"""

import socket
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from evals.serve_live import (
    API_LINE,
    HOST,
    IMMUTABLE,
    PRODUCTION_PORT,
    SAME_ORIGIN_LINE,
    UI_FILE,
    build_app,
    check_port,
    served_build,
    served_ui,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCE = UI_FILE.read_text(encoding="utf-8")


def test_served_copy_points_at_its_own_origin_and_the_file_stays_as_it_was():
    before = UI_FILE.read_bytes()
    page = served_ui(SOURCE, "v1")
    assert SAME_ORIGIN_LINE in page and API_LINE not in page
    assert "localhost:8000" not in page
    assert "not production" in page
    assert UI_FILE.read_bytes() == before
    assert API_LINE in UI_FILE.read_text(encoding="utf-8")


def test_changed_ui_address_stops_rather_than_calling_the_wrong_server():
    with pytest.raises(ValueError):
        served_ui(SOURCE.replace(API_LINE, 'const API = "http://example.com";'), "v1")


def test_production_port_is_refused():
    with pytest.raises(SystemExit, match="production"):
        check_port(PRODUCTION_PORT)


def test_busy_port_is_refused():
    with socket.socket() as s:
        s.bind((HOST, 0))
        s.listen()
        with pytest.raises(SystemExit, match="already listening"):
            check_port(s.getsockname()[1])


def test_it_only_listens_on_this_computer():
    assert HOST == "127.0.0.1"


def _client():
    api = FastAPI()

    @api.get("/api/stats")
    def stats():
        return {"from": "pipeline"}

    return TestClient(build_app(api, "<html>ui page</html>", {"production_api": False}))


def test_root_is_the_ui_and_api_paths_reach_the_pipeline():
    client = _client()
    assert "ui page" in client.get("/").text
    assert client.get("/api/stats").json() == {"from": "pipeline"}
    assert client.get("/live-info").json() == {"production_api": False}


@pytest.fixture
def built(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app-abc123.js").write_text("console.log('x');" * 100, encoding="utf-8")
    (tmp_path / "icons").mkdir()
    (tmp_path / "icons" / "icon.png").write_bytes(b"png")
    (tmp_path / "sw.js").write_text("// sw", encoding="utf-8")
    (tmp_path / "manifest.webmanifest").write_text("{}", encoding="utf-8")
    (tmp_path / "index.html").write_text("<html><head></head><body>new ui</body></html>", encoding="utf-8")
    return tmp_path


def _built_client(dist):
    api = FastAPI()

    @api.get("/api/stats")
    def stats():
        return {"from": "pipeline"}

    @api.post("/api/chat")
    def chat():
        return {"answer": "from pipeline"}

    page = served_build((dist / "index.html").read_text(encoding="utf-8"), "v1")
    return TestClient(build_app(api, page, {"production_api": False}, dist))


def test_built_page_says_its_a_test_copy_and_isnt_cached(built):
    response = _built_client(built).get("/")
    assert '<meta name="legalai-instance" content="v1">' in response.text
    assert "new ui" in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_a_production_build_has_no_test_copy_tag(built):
    assert "legalai-instance" not in (built / "index.html").read_text(encoding="utf-8")


def test_hashed_assets_cache_for_a_year_and_are_compressed(built):
    response = _built_client(built).get("/assets/app-abc123.js", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == IMMUTABLE
    assert response.headers["content-encoding"] == "gzip"


def test_service_worker_and_manifest_are_revalidated_at_the_root(built):
    client = _built_client(built)
    for path in ("/sw.js", "/manifest.webmanifest"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
    assert "javascript" in client.get("/sw.js").headers["content-type"]


def test_unknown_page_falls_back_to_the_app(built):
    response = _built_client(built).get("/some/page", headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert "new ui" in response.text


def test_the_fallback_never_hides_a_missing_api_or_asset(built):
    client = _built_client(built)
    assert client.get("/api/nothing", headers={"Accept": "text/html"}).status_code == 404
    assert client.get("/assets/missing.js", headers={"Accept": "*/*"}).status_code == 404


def test_api_still_reaches_the_pipeline_with_the_build_mounted(built):
    client = _built_client(built)
    assert client.get("/api/stats").json() == {"from": "pipeline"}
    response = client.post("/api/chat", json={})
    assert response.json() == {"answer": "from pipeline"}
    assert "content-encoding" not in response.headers


def test_built_page_needs_a_head_tag():
    with pytest.raises(ValueError):
        served_build("<html>no head</html>", "v1")


def test_the_real_api_routes_the_ui_calls_exist():
    api_source = (ROOT / "api_v3.py").read_text(encoding="utf-8")
    for path in ("/api/stats", "/api/chat", "/api/predict", "/api/search", "/api/generate-doc"):
        assert f'"{path}"' in api_source
        assert f"{{API}}{path}" in SOURCE
