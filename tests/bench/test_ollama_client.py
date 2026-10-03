"""The shared local-LLM client (training plan E2, reused by F1)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from bench.ollama import NotLoopback, OllamaClient, OllamaError, RawLog

SCHEMA = {"type": "object", "properties": {"payload": {"type": "string"}}, "required": ["payload"]}


class _Recorder:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, url: str, body: bytes, timeout: float) -> bytes:
        self.bodies.append(json.loads(body))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply).encode("utf-8")


def _ok(content: Any) -> dict[str, Any]:
    text = content if isinstance(content, str) else json.dumps(content)
    return {"message": {"role": "assistant", "content": text}, "eval_count": 7, "done": True}


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"],
)
def test_loopback_urls_are_accepted(url: str) -> None:
    OllamaClient(base_url=url, transport=_Recorder([]))


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0:11434",
        "http://192.168.1.5:11434",
        "http://ollama.example.invalid:11434",
        "https://127.0.0.1.example.invalid",
        "file:///etc/passwd",
    ],
)
def test_anything_but_loopback_is_refused(url: str) -> None:
    with pytest.raises(NotLoopback):
        OllamaClient(base_url=url, transport=_Recorder([]))


def test_a_request_is_deterministic_and_schema_bound() -> None:
    transport = _Recorder([_ok({"payload": "Use UTC."})])
    client = OllamaClient(transport=transport)
    reply = client.chat("m", [{"role": "user", "content": "hi"}], schema=SCHEMA, seed=11)
    assert reply.parsed == {"payload": "Use UTC."}
    body = transport.bodies[0]
    assert body["stream"] is False
    assert body["think"] is False
    assert body["format"] == SCHEMA
    assert body["options"]["temperature"] == 0
    assert body["options"]["seed"] == 11


def test_transport_and_parse_failures_are_retried_three_times_then_raised() -> None:
    transport = _Recorder([OSError("refused"), _ok("not json"), _ok({"payload": "ok."})])
    reply = OllamaClient(transport=transport).chat("m", [], schema=SCHEMA, seed=0)
    assert reply.parsed == {"payload": "ok."}
    assert reply.attempts == 3

    failing = _Recorder([OSError("a"), OSError("b"), _ok({"wrong": 1})])
    with pytest.raises(OllamaError, match="3 attempts"):
        OllamaClient(transport=failing).chat("m", [], schema=SCHEMA, seed=0)


def test_the_raw_log_resumes_by_key(tmp_path: Path) -> None:
    log = RawLog(tmp_path / "raw.jsonl")
    assert log.get("m", "row-1") is None
    log.put("m", "row-1", {"status": "accepted", "payload": "Use UTC."})
    log.put("m", "row-2", {"status": "dropped"})
    again = RawLog(tmp_path / "raw.jsonl")
    assert again.get("m", "row-1") == {"status": "accepted", "payload": "Use UTC."}
    assert again.get("other-model", "row-1") is None
    assert (tmp_path / "raw.jsonl").read_text(encoding="utf-8").count("\n") == 2


def test_the_default_transport_talks_http_to_a_loopback_server() -> None:
    seen: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the stdlib's name
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append({"path": self.path, "body": json.loads(body)})
            payload = json.dumps(_ok({"payload": "Fine."})).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = OllamaClient(base_url=f"http://127.0.0.1:{server.server_port}")
        reply = client.chat("m", [{"role": "user", "content": "x"}], schema=SCHEMA, seed=1)
    finally:
        server.shutdown()
        server.server_close()
    assert reply.parsed == {"payload": "Fine."}
    assert seen[0]["path"] == "/api/chat"
