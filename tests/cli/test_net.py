"""Unit tests for `sieve.cli.net`: the offline socket guard and the `--api-url` client.

Uses `httpx.MockTransport` for the client (never a real socket) and a monkeypatched
`socket.socket.connect` for the guard (never a real connection either).
"""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import huggingface_hub
import pytest

from sieve.cli import net
from sieve.protocols import FakeClassifier
from sieve.types import ToolDefinition, ToolRecord, TriageResult


def _record(
    *, description: str | None = "Reads a file.", instructions: str | None = None
) -> ToolRecord:
    return ToolRecord(
        id="fs/read_file",
        server_name="fs",
        tool=ToolDefinition(
            name="read_file",
            description=description,
            inputSchema={"type": "object", "properties": {}},
        ),
        sibling_tool_names=["write_file"],
        server_instructions=instructions,
    )


def _result(**overrides: object) -> TriageResult:
    base = dict(
        verdict="dismissed",
        stage="classifier",
        predicted_class="benign",
        class_probabilities={"benign": 0.9, "imperative_benign": 0.05, "poisoned": 0.05},
        poisoned_probability=0.05,
        confidence=0.9,
        sub_labels=[],
        owasp=[],
        evidence_spans=[],
        unlocated_quotes=0,
        reasoning=None,
        remediation=None,
        scanner_findings=[],
        model_version="fake@0",
        judge_model=None,
        latency_ms=1,
    )
    base.update(overrides)
    return TriageResult.model_validate(base)


# --- the socket guard -------------------------------------------------------------------------


def test_guard_starts_uninstalled() -> None:
    assert net.guard_installed() is False


def test_install_guard_blocks_socket_connect() -> None:
    net.install_guard()
    assert net.guard_installed() is True
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(net.NetworkBlocked, match="offline mode"),
    ):
        sock.connect(("example.invalid", 80))


def test_install_guard_is_idempotent() -> None:
    net.install_guard()
    net.install_guard()  # must not raise, must not double-wrap
    assert net.guard_installed() is True


def test_reset_guard_for_tests_restores_real_connect() -> None:
    real_connect = socket.socket.connect
    net.install_guard()
    net._reset_guard_for_tests()
    assert net.guard_installed() is False
    assert socket.socket.connect is real_connect


# --- ensure_model_cached / load_classifier_offline --------------------------------------------


def test_ensure_model_cached_prefers_a_local_directory(tmp_path: Path) -> None:
    model_dir = tmp_path / "local-model"
    model_dir.mkdir()
    assert net.ensure_model_cached(str(model_dir), revision=None, cache_dir=tmp_path) == str(
        model_dir
    )


def test_ensure_model_cached_hits_cache_first_with_no_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[bool] = []

    def fake_snapshot_download(
        repo_id: str, *, revision: str | None, cache_dir: str, local_files_only: bool = False
    ) -> str:
        calls.append(local_files_only)
        assert local_files_only is True
        return str(tmp_path / "cached")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    result = net.ensure_model_cached("org/model", revision=None, cache_dir=tmp_path)
    assert result == str(tmp_path / "cached")
    assert calls == [True]


def test_ensure_model_cached_falls_back_to_the_hub_once_on_a_cache_miss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[bool] = []

    def fake_snapshot_download(
        repo_id: str, *, revision: str | None, cache_dir: str, local_files_only: bool = False
    ) -> str:
        calls.append(local_files_only)
        if local_files_only:
            raise huggingface_hub.errors.LocalEntryNotFoundError("not cached")
        return str(tmp_path / "downloaded")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    result = net.ensure_model_cached("org/model", revision=None, cache_dir=tmp_path)
    assert result == str(tmp_path / "downloaded")
    assert calls == [True, False]


def test_load_classifier_offline_installs_the_guard_for_the_fixture_model(tmp_path: Path) -> None:
    classifier = net.load_classifier_offline("fixture", revision=None, cache_dir=tmp_path)
    assert isinstance(classifier, FakeClassifier)
    assert net.guard_installed() is True


def test_load_classifier_offline_installs_the_guard_after_a_real_download(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_dir = tmp_path / "downloaded"
    model_dir.mkdir()

    monkeypatch.setattr(
        huggingface_hub,
        "snapshot_download",
        lambda repo_id, *, revision, cache_dir, local_files_only=False: (
            (_ for _ in ()).throw(huggingface_hub.errors.LocalEntryNotFoundError("miss"))
            if local_files_only
            else str(model_dir)
        ),
    )
    monkeypatch.setattr(net, "load_classifier", lambda *a, **kw: FakeClassifier())
    classifier = net.load_classifier_offline("org/model", revision=None, cache_dir=tmp_path)
    assert isinstance(classifier, FakeClassifier)
    assert net.guard_installed() is True


# --- TriageClient ------------------------------------------------------------------------------


def test_triage_client_sends_redacted_text_only() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json=_result().model_dump(mode="json"))

    transport = httpx.MockTransport(handler)
    with net.TriageClient("http://testserver", transport=transport) as client:
        record = _record(
            description="A perfectly ordinary tool description.",
            instructions="api_key=sk-ant-abcdefghijklmnopqrstuvwx0123456789",
        )
        result = client.triage_tool(record)

    assert result.verdict == "dismissed"
    body = captured["body"]
    assert isinstance(body, bytes)
    assert b"sk-ant-" not in body
    assert b"[REDACTED:anthropic_key]" in body


def test_triage_client_raises_api_error_on_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="nope")

    transport = httpx.MockTransport(handler)
    with (
        net.TriageClient("http://testserver", transport=transport) as client,
        pytest.raises(net.ApiError, match="422"),
    ):
        client.triage_tool(_record())


def test_triage_client_wraps_transport_errors_as_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    transport = httpx.MockTransport(handler)
    with (
        net.TriageClient("http://testserver", transport=transport) as client,
        pytest.raises(net.ApiError),
    ):
        client.triage_tool(_record())
