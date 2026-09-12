import json
from pathlib import Path

from fastapi.testclient import TestClient

from sieve.api.main import create_app

client = TestClient(create_app(stub=True))

PATHS = [
    ("POST", "/api/v1/scans"),
    ("GET", "/api/v1/scans/{scan_id}"),
    ("GET", "/api/v1/scans/{scan_id}/events"),
    ("GET", "/api/v1/scans/{scan_id}/findings"),
    ("GET", "/api/v1/scans/{scan_id}/sarif"),
    ("POST", "/api/v1/triage"),
    ("POST", "/api/v1/findings/{finding_id}/feedback"),
    ("GET", "/api/v1/bench/summary"),
    ("GET", "/api/v1/models"),
    ("GET", "/api/v1/healthz"),
    ("GET", "/api/v1/readyz"),
]


def test_all_paths_exist_in_openapi() -> None:
    spec = client.get("/api/v1/openapi.json").json()
    for method, path in PATHS:
        assert method.lower() in spec["paths"][path], (method, path)


def test_stub_scan_roundtrip() -> None:
    tools = json.loads(Path("fixtures/tools/sample-27.json").read_text(encoding="utf-8"))
    r = client.post("/api/v1/scans", json={"source_type": "tools_json", "tools_json": tools})
    assert r.status_code == 202
    scan_id = r.json()["id"]
    assert client.get(f"/api/v1/scans/{scan_id}").json()["tool_count"] == 27
    assert client.get(f"/api/v1/scans/{scan_id}/findings").json()["total"] == 27


def test_committed_openapi_matches_live() -> None:
    live = client.get("/api/v1/openapi.json").json()
    committed = json.loads(Path("schemas/openapi.json").read_text(encoding="utf-8"))
    assert live == committed, "run `make schemas` and commit the result"


def test_stub_models_reports_judge_disabled() -> None:
    info = client.get("/api/v1/models").json()
    assert info["judge_enabled"] is False and info["model_version"] == "fixture@0"
