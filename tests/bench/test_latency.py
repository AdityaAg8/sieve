"""Task 11.2 -- `bench/latency.py`'s percentile math and request orchestration.

No live server: `run()` takes an `httpx.MockTransport` so concurrency and the percentile/report
shape are exercised with no network and no Postgres. The one number this suite does not (and
cannot) check is what a real deployment's p95 actually is -- that comes from running the script
for real against a deployment. Such a smoke number is never committed to `bench/results/`
(PRD M6a).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from bench import latency

MODEL_RESPONSE = {"model_version": "fixture@0", "t_low": 0.3, "t_high": 0.85}
TRIAGE_RESPONSE = {
    "verdict": "dismissed",
    "stage": "classifier",
    "predicted_class": "benign",
    "class_probabilities": {"benign": 0.9, "imperative_benign": 0.05, "poisoned": 0.05},
    "poisoned_probability": 0.05,
    "confidence": 0.9,
    "sub_labels": [],
    "owasp": [],
    "evidence_spans": [],
    "unlocated_quotes": 0,
    "reasoning": None,
    "remediation": None,
    "model_version": "fixture@0",
    "judge_model": None,
    "latency_ms": 1,
}


def _handler(*, delay_s: float = 0.0, concurrency_probe: dict[str, int] | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == latency.MODELS_PATH:
            return httpx.Response(200, json=MODEL_RESPONSE)
        assert request.url.path == latency.TRIAGE_PATH
        if concurrency_probe is not None:
            concurrency_probe["current"] += 1
            concurrency_probe["max"] = max(concurrency_probe["max"], concurrency_probe["current"])
        if delay_s:
            await asyncio.sleep(delay_s)
        if concurrency_probe is not None:
            concurrency_probe["current"] -= 1
        return httpx.Response(200, json=TRIAGE_RESPONSE)

    return handler


async def test_run_makes_n_requests_and_reports_the_deployments_model_id() -> None:
    transport = httpx.MockTransport(_handler())
    latencies, model_id = await latency.run(
        "http://test", n=17, concurrency=5, payload=latency.DEFAULT_TOOL, transport=transport
    )
    assert len(latencies) == 17
    assert all(ms >= 0.0 for ms in latencies)
    assert model_id == "fixture@0"


async def test_run_never_exceeds_the_requested_concurrency() -> None:
    probe = {"current": 0, "max": 0}
    transport = httpx.MockTransport(_handler(delay_s=0.02, concurrency_probe=probe))
    await latency.run(
        "http://test", n=20, concurrency=4, payload=latency.DEFAULT_TOOL, transport=transport
    )
    assert probe["max"] <= 4
    # With 20 requests, a 20ms delay and concurrency 4, the cap should actually be reached --
    # otherwise a bug that serialises everything (e.g. a missing `async with sem`) would also
    # pass a "never exceeds" assertion alone.
    assert probe["max"] == 4


async def test_run_surfaces_a_triage_error_instead_of_hanging() -> None:
    async def failing(request: httpx.Request) -> httpx.Response:
        if request.url.path == latency.MODELS_PATH:
            return httpx.Response(200, json=MODEL_RESPONSE)
        return httpx.Response(500, json={"detail": "boom"})

    transport = httpx.MockTransport(failing)
    with pytest.raises(httpx.HTTPStatusError):
        await latency.run(
            "http://test", n=3, concurrency=2, payload=latency.DEFAULT_TOOL, transport=transport
        )


def test_percentiles_are_monotonic_and_within_the_sample_range() -> None:
    latencies = [1.0, 2.0, 3.0, 4.0, 100.0]
    result = latency.percentiles(latencies)
    assert result["p50_ms"] <= result["p95_ms"] <= result["p99_ms"]
    assert min(latencies) <= result["p50_ms"] <= max(latencies)


def test_build_report_carries_the_requested_shape() -> None:
    report = latency.build_report([1.0, 2.0, 3.0], model_id="fixture@0", n=3, concurrency=20)
    assert report["model_id"] == "fixture@0"
    assert report["n"] == 3
    assert report["concurrency"] == 20
    assert report["utc"].endswith("Z")
    assert set(report) >= {
        "model_id",
        "git_sha",
        "utc",
        "n",
        "concurrency",
        "p50_ms",
        "p95_ms",
        "p99_ms",
    }


def test_write_report_is_utf8_with_unix_newlines(tmp_path: Path) -> None:
    report = latency.build_report([1.0, 2.0], model_id="fixture@0", n=2, concurrency=20)
    path = latency.write_report(report, tmp_path / "latency.json")

    raw = path.read_bytes()
    assert b"\r\n" not in raw
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == report


def test_main_writes_bench_results_latency_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = httpx.MockTransport(_handler())
    real_run = latency.run
    monkeypatch.setattr(
        latency,
        "run",
        lambda base_url, *, n, concurrency, payload: real_run(
            base_url, n=n, concurrency=concurrency, payload=payload, transport=transport
        ),
    )
    report = latency.main(["--n", "6", "--concurrency", "3", "--results-dir", str(tmp_path)])

    written = json.loads((tmp_path / "latency.json").read_text(encoding="utf-8"))
    assert written == report
    assert report["n"] == 6
    assert report["concurrency"] == 3


def _stub_run(model_id: str) -> object:
    async def fake_run(
        base_url: str, *, n: int, concurrency: int, payload: dict[str, object]
    ) -> tuple[list[float], str]:
        return [1.0, 2.0, 3.0], model_id

    return fake_run


def test_main_refuses_to_write_under_bench_results_for_the_fixture_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD M6a: `bench/results/` holds only generated, *published* artefacts.

    `monkeypatch.chdir` isolates the default `--results-dir` (`bench/results`, a relative path)
    under `tmp_path` -- so even if the guard this test exists to prove were broken, the failure
    would land in a throwaway directory, never in the real repository's `bench/results/`.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(latency, "run", _stub_run("fixture@0"))

    with pytest.raises(SystemExit, match="refusing to write"):
        latency.main([])

    assert not (tmp_path / "bench" / "results" / "latency.json").exists()


def test_main_still_writes_a_fixture_run_outside_bench_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is about the *destination*, not the model -- a smoke run naming its own
    directory (outside `bench/results/`) is exactly what the fixture classifier is for."""
    monkeypatch.setattr(latency, "run", _stub_run("fixture@0"))
    out_dir = tmp_path / "smoke"

    report = latency.main(["--results-dir", str(out_dir)])

    assert json.loads((out_dir / "latency.json").read_text(encoding="utf-8")) == report
    assert report["model_id"] == "fixture@0"


def test_main_writes_a_real_model_id_to_the_default_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(latency, "run", _stub_run("deberta-v3-s0@abc1234"))

    report = latency.main([])

    path = tmp_path / "bench" / "results" / "latency.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report
    assert report["model_id"] == "deberta-v3-s0@abc1234"


async def test_run_gives_a_clear_message_when_nothing_is_listening() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(refuse)
    with pytest.raises(SystemExit, match="nothing listening"):
        await latency.run(
            "http://test", n=1, concurrency=1, payload=latency.DEFAULT_TOOL, transport=transport
        )
