"""`sieve triage`: CLI-level tests via Typer's `CliRunner`.

These drive the real pipeline (`FakeClassifier`/`FakeJudge` through `sieve.pipeline.triage_many`,
never a mock of the pipeline itself); only the two genuine network boundaries -- the Hub download
and the `--api-url` HTTP client -- are faked, per the lane brief's self-review checklist.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import huggingface_hub
import pytest
from typer.testing import CliRunner

from sieve.cli import net
from sieve.cli.app import EXIT_FINDINGS, EXIT_INPUT, EXIT_MODEL, EXIT_OK, app
from sieve.protocols import FakeClassifier
from sieve.types import Finding, ToolRecord, TriageResult

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
TOOLS_27 = FIXTURES / "tools" / "sample-27.json"
CISCO_GOLDEN = FIXTURES / "scanner" / "cisco-yara-sample-27.json"
MCP_CONFIG = FIXTURES / "mcp-config" / "sample.mcp.json"

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the real Anthropic judge out of every test unless one opts in explicitly."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# --- Step 1: the fixture in, JSON out, `--fail-on`, `--ci` -------------------------------------


def test_format_json_validates_as_a_list_of_finding() -> None:
    result = runner.invoke(app, ["triage", str(TOOLS_27), "--format", "json", "--no-judge"])
    # 5 of the 27 fixture tools score >= t_high regardless of the judge; default --fail-on is
    # "confirmed", so this always exits 1.
    assert result.exit_code == EXIT_FINDINGS, result.output
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 27
    findings = [Finding.model_validate(item) for item in payload]
    assert {f.tool.name for f in findings} == {
        t["name"] for t in json.loads(TOOLS_27.read_text(encoding="utf-8"))["tools"]
    }


def test_from_cisco_yara_reads_the_golden_from_stdin_and_attaches_scanner_findings() -> None:
    stdin_text = CISCO_GOLDEN.read_text(encoding="utf-8")
    result = runner.invoke(
        app,
        ["triage", "-", "--from", "cisco-yara", "--no-judge", "--format", "json"],
        input=stdin_text,
    )
    # A `--from` record carries no description, so the classifier has nothing poison-worded to
    # score: all 27 are dismissed, so the default --fail-on "confirmed" exits 0.
    assert result.exit_code == EXIT_OK, result.output
    findings = [Finding.model_validate(item) for item in json.loads(result.stdout)]
    assert len(findings) == 27
    by_name = {f.tool.name: f for f in findings}
    assert by_name["export_csv"].result.scanner_findings
    assert by_name["export_csv"].result.scanner_findings[0].rule_id == "credential_harvesting"
    # A clean tool per the scanner still gets a full triage result, with zero scanner findings.
    assert by_name["click"].result.scanner_findings == []


def test_fail_on_confirmed_exits_1_on_the_fixture() -> None:
    result = runner.invoke(
        app, ["triage", str(TOOLS_27), "--fail-on", "confirmed", "--no-judge", "--format", "json"]
    )
    assert result.exit_code == EXIT_FINDINGS, result.output


def test_fail_on_never_exits_0_on_the_same_fixture() -> None:
    result = runner.invoke(
        app, ["triage", str(TOOLS_27), "--fail-on", "never", "--no-judge", "--format", "json"]
    )
    assert result.exit_code == EXIT_OK, result.output


def test_fail_on_any_exits_0_without_any_scanner_findings() -> None:
    # Fix round 1, Important 1: `any` means "the scanner flagged something", not "anything was
    # triaged" -- a plain tools.json carries no scanner findings at all, so `any` must not fire
    # just because 27 tools were scored.
    result = runner.invoke(
        app, ["triage", str(TOOLS_27), "--fail-on", "any", "--no-judge", "--format", "json"]
    )
    assert result.exit_code == EXIT_OK, result.output


def test_fail_on_any_exits_1_on_the_cisco_yara_golden_even_when_every_tool_is_dismissed() -> None:
    stdin_text = CISCO_GOLDEN.read_text(encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "triage",
            "-",
            "--from",
            "cisco-yara",
            "--no-judge",
            "--fail-on",
            "any",
            "--format",
            "json",
        ],
        input=stdin_text,
    )
    assert result.exit_code == EXIT_FINDINGS, result.output
    # The claim in the name: every one of the 27 tools is dismissed (a `--from` record carries no
    # description, so the classifier has nothing poison-worded to score), yet 4 of them were
    # scanner-flagged and `any` still fires.
    findings = [Finding.model_validate(item) for item in json.loads(result.stdout)]
    assert all(f.result.verdict == "dismissed" for f in findings)
    assert sum(1 for f in findings if f.result.scanner_findings) == 4


def test_ci_alone_is_format_json_and_fail_on_confirmed() -> None:
    plain = runner.invoke(
        app, ["triage", str(TOOLS_27), "--format", "json", "--fail-on", "confirmed", "--no-judge"]
    )
    via_ci = runner.invoke(app, ["triage", str(TOOLS_27), "--ci", "--no-judge"])
    assert via_ci.exit_code == plain.exit_code == EXIT_FINDINGS
    # `id`/`scan_id` are a fresh uuid4 per invocation; compare the triage results themselves.
    ci_results = [item["result"] for item in json.loads(via_ci.stdout)]
    plain_results = [item["result"] for item in json.loads(plain.stdout)]
    assert ci_results == plain_results


def test_ci_with_a_conflicting_fail_on_exits_2_and_names_both_flags() -> None:
    result = runner.invoke(app, ["triage", str(TOOLS_27), "--ci", "--fail-on", "never"])
    assert result.exit_code == EXIT_INPUT
    assert "--ci" in result.output
    assert "--fail-on" in result.output


def test_ci_with_a_conflicting_format_exits_2() -> None:
    result = runner.invoke(app, ["triage", str(TOOLS_27), "--ci", "--format", "pretty"])
    assert result.exit_code == EXIT_INPUT


def test_threshold_override_prints_a_warning_and_still_runs() -> None:
    result = runner.invoke(
        app,
        [
            "triage",
            str(TOOLS_27),
            "--threshold-override",
            "0.1,0.2",
            "--no-judge",
            "--fail-on",
            "never",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    assert "warning" in result.output.lower()
    assert "--threshold-override" in result.output
    # A far lower t_high should push more tools to "confirmed" than the published thresholds.
    findings = [Finding.model_validate(item) for item in json.loads(result.stdout)]
    assert any(f.result.verdict == "confirmed" for f in findings)


def test_threshold_override_rejects_a_malformed_value() -> None:
    result = runner.invoke(app, ["triage", str(TOOLS_27), "--threshold-override", "not-a-pair"])
    assert result.exit_code == EXIT_INPUT


# --- Step 2: --offline ---------------------------------------------------------------------


def test_offline_with_the_fixture_model_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, *a, **kw: calls.append(a) or None)
    result = runner.invoke(
        app, ["triage", str(TOOLS_27), "--offline", "--format", "json", "--fail-on", "never"]
    )
    assert result.exit_code == EXIT_OK, result.output
    assert calls == []
    assert net.guard_installed() is True


def test_offline_with_an_empty_cache_contacts_the_hub_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connect_calls = {"n": 0}
    monkeypatch.setattr(
        socket.socket,
        "connect",
        lambda self, *a, **kw: connect_calls.__setitem__("n", connect_calls["n"] + 1),
    )

    def fake_snapshot_download(
        repo_id: str, *, revision: str | None, cache_dir: str, local_files_only: bool = False
    ) -> str:
        if local_files_only:
            raise huggingface_hub.errors.LocalEntryNotFoundError("not cached")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.connect(("huggingface.co", 443))
        return str(tmp_path / "downloaded")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr(net, "load_classifier", lambda *a, **kw: FakeClassifier())

    result = runner.invoke(
        app,
        [
            "triage",
            str(TOOLS_27),
            "--offline",
            "--model",
            "org/model",
            "--format",
            "json",
            "--fail-on",
            "never",
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    assert connect_calls["n"] == 1
    assert net.guard_installed() is True


def test_offline_with_a_populated_cache_opens_no_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connect_calls = {"n": 0}
    monkeypatch.setattr(
        socket.socket,
        "connect",
        lambda self, *a, **kw: connect_calls.__setitem__("n", connect_calls["n"] + 1),
    )

    def fake_snapshot_download(
        repo_id: str, *, revision: str | None, cache_dir: str, local_files_only: bool = False
    ) -> str:
        assert local_files_only is True
        return str(tmp_path / "cached")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    monkeypatch.setattr(net, "load_classifier", lambda *a, **kw: FakeClassifier())

    result = runner.invoke(
        app,
        [
            "triage",
            str(TOOLS_27),
            "--offline",
            "--model",
            "org/model",
            "--format",
            "json",
            "--fail-on",
            "never",
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    assert connect_calls["n"] == 0
    assert net.guard_installed() is True


def test_offline_blocks_a_subsequent_api_call_and_exits_3() -> None:
    result = runner.invoke(
        app,
        [
            "triage",
            str(TOOLS_27),
            "--offline",
            "--api-url",
            "http://127.0.0.1:1/",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == EXIT_MODEL, result.output
    assert "offline mode: network access refused after the model download" in result.output


# --- --api-url ---------------------------------------------------------------------------------


def test_api_url_posts_one_redacted_tool_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_bodies.append(request.read())
        body = json.loads(seen_bodies[-1])
        record = ToolRecord.model_validate(body)
        result = TriageResult(
            verdict="dismissed",
            stage="classifier",
            predicted_class="benign",
            class_probabilities={"benign": 0.9, "imperative_benign": 0.05, "poisoned": 0.05},
            poisoned_probability=0.05,
            confidence=0.9,
            sub_labels=[],
            owasp=[],
            evidence_spans=[],
            reasoning=None,
            remediation=None,
            scanner_findings=[],
            model_version="remote@0",
            judge_model=None,
            latency_ms=3,
        )
        assert record.tool.name  # every posted body is a real ToolRecord
        return httpx.Response(200, json=result.model_dump(mode="json"))

    transport = httpx.MockTransport(handler)
    real_triage_client = net.TriageClient
    monkeypatch.setattr(
        net,
        "TriageClient",
        lambda base_url, **kw: real_triage_client(base_url, transport=transport),
    )

    result = runner.invoke(
        app,
        [
            "triage",
            str(TOOLS_27),
            "--api-url",
            "http://testserver",
            "--format",
            "json",
            "--fail-on",
            "never",
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    assert len(seen_bodies) == 27
    findings = [Finding.model_validate(item) for item in json.loads(result.stdout)]
    assert all(f.result.model_version == "remote@0" for f in findings)


def _patch_api_url_transport(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    real_triage_client = net.TriageClient
    monkeypatch.setattr(
        net,
        "TriageClient",
        lambda base_url, **kw: real_triage_client(base_url, transport=transport),
    )


def test_api_url_with_a_200_invalid_json_response_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix round 1, Important 2 (CliRunner coverage): a malformed 200 used to leak a bare
    # traceback instead of a clean exit 3.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{not json")

    _patch_api_url_transport(monkeypatch, handler)
    result = runner.invoke(
        app, ["triage", str(TOOLS_27), "--api-url", "http://testserver", "--fail-on", "never"]
    )
    assert result.exit_code == EXIT_MODEL, result.output


def test_api_url_with_a_200_wrong_shape_response_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "a TriageResult"})

    _patch_api_url_transport(monkeypatch, handler)
    result = runner.invoke(
        app, ["triage", str(TOOLS_27), "--api-url", "http://testserver", "--fail-on", "never"]
    )
    assert result.exit_code == EXIT_MODEL, result.output


# --- --config PATH --static (SC-1: parse only) --------------------------------------------------


def test_config_static_never_produces_a_tool_and_prints_the_clean_scan_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect_calls: list[object] = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, *a, **kw: connect_calls.append(a))
    result = runner.invoke(app, ["triage", "--config", str(MCP_CONFIG), "--static", "--no-judge"])
    assert result.exit_code == EXIT_OK, result.output
    assert connect_calls == []
    assert "No findings to triage. The scanner flagged nothing in these 0 tools." in result.output


def test_config_static_never_loads_a_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    # Fix round 1, Minor 1: zero ToolRecords means nothing to score, so no classifier -- local or
    # offline -- should ever be constructed. The Action hits this path on every PR.
    def boom(*_a: object, **_kw: object) -> object:
        raise AssertionError("the classifier must not be loaded when there is nothing to triage")

    monkeypatch.setattr("sieve.cli.app.load_classifier", boom)
    monkeypatch.setattr(net, "load_classifier_offline", boom)
    result = runner.invoke(
        app, ["triage", "--config", str(MCP_CONFIG), "--static", "--offline", "--format", "json"]
    )
    assert result.exit_code == EXIT_OK, result.output
    assert json.loads(result.stdout) == []


def test_config_without_static_is_an_input_error() -> None:
    result = runner.invoke(app, ["triage", "--config", str(MCP_CONFIG)])
    assert result.exit_code == EXIT_INPUT


def test_config_and_input_together_is_an_input_error() -> None:
    result = runner.invoke(app, ["triage", str(TOOLS_27), "--config", str(MCP_CONFIG), "--static"])
    assert result.exit_code == EXIT_INPUT


def test_neither_input_nor_config_is_an_input_error() -> None:
    result = runner.invoke(app, ["triage"])
    assert result.exit_code == EXIT_INPUT


def test_missing_input_file_is_an_input_error() -> None:
    result = runner.invoke(app, ["triage", "does-not-exist.json"])
    assert result.exit_code == EXIT_INPUT


def test_invalid_json_input_is_an_input_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    result = runner.invoke(app, ["triage", str(bad)])
    assert result.exit_code == EXIT_INPUT


def test_invalid_format_is_an_input_error() -> None:
    result = runner.invoke(app, ["triage", str(TOOLS_27), "--format", "yaml"])
    assert result.exit_code == EXIT_INPUT


def test_invalid_fail_on_is_an_input_error() -> None:
    result = runner.invoke(app, ["triage", str(TOOLS_27), "--fail-on", "sometimes"])
    assert result.exit_code == EXIT_INPUT


# --- pretty output and --output -----------------------------------------------------------------


def test_pretty_output_shows_confirmed_before_dismissed_and_never_greens_dismissed() -> None:
    result = runner.invoke(app, ["triage", str(TOOLS_27), "--no-judge"])
    assert result.exit_code == EXIT_FINDINGS, result.output  # the fixture always has a confirmed
    confirmed_at = result.output.find("Confirmed")
    dismissed_at = result.output.find("Dismissed")
    assert confirmed_at != -1
    assert dismissed_at != -1
    assert confirmed_at < dismissed_at
    assert "\x1b[32m" not in result.output  # never rendered in ANSI green


def test_output_flag_writes_to_a_file_not_stdout(tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    result = runner.invoke(
        app, ["triage", str(TOOLS_27), "--format", "json", "--output", str(out), "--no-judge"]
    )
    assert result.exit_code == EXIT_FINDINGS, result.output  # the fixture always has a confirmed
    assert result.stdout == ""
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 27


def test_format_sarif_validates_via_to_sarif(tmp_path: Path) -> None:
    out = tmp_path / "out.sarif"
    result = runner.invoke(
        app,
        [
            "triage",
            str(TOOLS_27),
            "--format",
            "sarif",
            "--output",
            str(out),
            "--no-judge",
            "--fail-on",
            "never",
        ],
    )
    assert result.exit_code == EXIT_OK, result.output
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["version"] == "2.1.0"
    assert len(doc["runs"][0]["results"]) == 27


def test_version_prints_the_installed_version() -> None:
    from sieve import __version__

    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__
