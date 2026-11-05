"""Live view এর নিরাপত্তা: UI আর API file অপরিবর্তিত, production port নয়, শুধু এই computer। api_v3 load করে না, তাই দ্রুত।"""

import socket
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from evals.serve_live import API_LINE, HOST, PRODUCTION_PORT, SAME_ORIGIN_LINE, UI_FILE, build_app, check_port, served_ui

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


def test_the_real_api_routes_the_ui_calls_exist():
    api_source = (ROOT / "api_v3.py").read_text(encoding="utf-8")
    for path in ("/api/stats", "/api/chat", "/api/predict", "/api/search", "/api/generate-doc"):
        assert f'"{path}"' in api_source
        assert f"{{API}}{path}" in SOURCE
