"""Task 11.2 -- p50/p95/p99 for `POST /api/v1/triage` at fixed concurrency.

`hey`/`oha` are not installed on the dev box, so this plays their role with `httpx`: `n`
requests against a *running* Sieve API (`python -m sieve.api`, or a deployment's own base URL),
at most `concurrency` in flight at once, timed end to end (connect through response body).

Wired into the Makefile `bench` target so a latency figure ships with every published run --
but PRD M6a's rule still holds: `bench/results/latency.json` is a generated artefact, never
hand-edited, and a number this script produces on a box with no trained ONNX int8 artefact
(`SIEVE_MODEL_ID=fixture`, the classifier `sample-27.json`'s scan runs on) is a smoke number,
not a published one -- see docs/handoffs/task-11.2.md. `model_id` is read back from the
deployment's own `GET /api/v1/models` rather than taken on faith from an argument, so the
figure can never be mislabelled as measuring an artefact the server was not actually running --
and when that read-back id is the fixture/fake classifier, `main()` refuses to write anywhere
under `bench/results/` at all (a non-`bench/results/` `--results-dir` is always still accepted,
for a smoke run). A preflight `GET /api/v1/models` also means a missing server fails fast, with
a clear message, before any of `n` requests are attempted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np

from sieve.model.version import git_sha
from sieve.protocols import FakeClassifier

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_CONCURRENCY = 20
#: `sieve.api.ratelimit.WINDOWS["triage_min"]` is 60 requests per 60 s per IP; comfortably
#: under that keeps a default run from being cut short by a 429 partway through.
DEFAULT_N = 50
TRIAGE_PATH = "/api/v1/triage"
MODELS_PATH = "/api/v1/models"

#: What `GET /models` reports when the fixture/fake classifier is answering -- never a
#: published number (PRD M6a). Both the real app's shared classifier (`FakeClassifier.
#: model_version`, "fake@0", when `SIEVE_MODEL_ID=fixture`) and the Phase 0 stub app's own
#: literal ("fixture@0", `sieve/api/routes/models.py`) are covered, since `--base-url` could
#: name either kind of deployment.
FIXTURE_MODEL_IDS = frozenset({FakeClassifier.model_version, "fixture@0"})

#: The one directory `make bench` (and PRD M6a) ever writes published numbers to; a fixture
#: run may write anywhere else (a smoke run against a tmp dir), never here.
BENCH_RESULTS_DIR = Path("bench/results")

#: A small, realistic tool -- enough to exercise a real classifier forward pass, not a
#: pathological input (an empty description, say) the pipeline would short-circuit on.
DEFAULT_TOOL: dict[str, Any] = {
    "id": "bench/latency_probe",
    "server_name": "bench",
    "tool": {
        "name": "latency_probe",
        "title": "Latency probe",
        "description": "Get current weather conditions for a location.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City name or 'lat,lon'."}
            },
            "required": ["location"],
        },
    },
    "sibling_tool_names": [],
}


def _is_under(path: Path, ancestor: Path) -> bool:
    """True when `path` is `ancestor` itself or somewhere inside it, resolved (`..`, symlinks)
    rather than compared textually -- `--results-dir bench/results/../results` must count."""
    try:
        path.resolve().relative_to(ancestor.resolve())
    except ValueError:
        return False
    return True


async def _one(client: httpx.AsyncClient, sem: asyncio.Semaphore, payload: dict[str, Any]) -> float:
    """One request's wall time in ms, from just before the call to the parsed response body."""
    async with sem:
        started = time.perf_counter()
        response = await client.post(TRIAGE_PATH, json=payload)
        response.raise_for_status()
        response.json()
        return (time.perf_counter() - started) * 1000.0


async def run(
    base_url: str,
    *,
    n: int,
    concurrency: int,
    payload: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[list[float], str]:
    """`n` requests at `concurrency` concurrent, and the deployment's active `model_version`.

    `transport` is the test seam: `None` dials `base_url` for real; a test passes an
    `httpx.MockTransport` so the concurrency and percentile logic runs with no server at all.
    """
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(
        base_url=base_url, limits=limits, timeout=30.0, transport=transport
    ) as client:
        # Preflight: the first request this script makes, before spending any of `n` on a
        # server that may not exist. A refused connection surfaces as a clear instruction
        # rather than an `httpx.ConnectError` traceback.
        try:
            model_response = await client.get(MODELS_PATH)
        except httpx.ConnectError as exc:
            raise SystemExit(
                f"nothing listening at {base_url} -- start the API first "
                f"(`python -m sieve.api`), or pass --base-url"
            ) from exc
        model_response.raise_for_status()
        model_id = str(model_response.json()["model_version"])

        sem = asyncio.Semaphore(concurrency)
        latencies = list(await asyncio.gather(*(_one(client, sem, payload) for _ in range(n))))
    return latencies, model_id


def percentiles(latencies: Sequence[float]) -> dict[str, float]:
    p50, p95, p99 = np.percentile(np.asarray(latencies, dtype=np.float64), [50, 95, 99])
    return {
        "p50_ms": round(float(p50), 3),
        "p95_ms": round(float(p95), 3),
        "p99_ms": round(float(p99), 3),
    }


def build_report(
    latencies: Sequence[float], *, model_id: str, n: int, concurrency: int
) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "git_sha": git_sha(short=False),
        "utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "n": n,
        "concurrency": concurrency,
        **percentiles(latencies),
    }


def write_report(report: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return path


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(prog="python -m bench.latency")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="a running Sieve API")
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--results-dir", type=Path, default=BENCH_RESULTS_DIR)
    parser.add_argument(
        "--tool",
        type=Path,
        default=None,
        help="a ToolRecord JSON body; defaults to a small built-in weather tool",
    )
    ns = parser.parse_args(argv)

    payload = json.loads(ns.tool.read_text(encoding="utf-8")) if ns.tool else DEFAULT_TOOL
    latencies, model_id = asyncio.run(
        run(ns.base_url, n=ns.n, concurrency=ns.concurrency, payload=payload)
    )
    report = build_report(latencies, model_id=model_id, n=ns.n, concurrency=ns.concurrency)
    print(json.dumps(report, indent=2, sort_keys=True))

    path = ns.results_dir / "latency.json"
    if model_id in FIXTURE_MODEL_IDS and _is_under(ns.results_dir, BENCH_RESULTS_DIR):
        # PRD M6a: bench/results/ holds only generated, *published* artefacts. A run against
        # the fixture/fake classifier (no trained ONNX int8 artefact, or a stub app) is a smoke
        # number, never that -- see docs/handoffs/task-11.2.md. Quoted above, not written here.
        raise SystemExit(
            f"refusing to write {path}: model_id={model_id!r} is the fixture/fake classifier, "
            f"not a published artefact (PRD M6a) -- pass --results-dir outside {BENCH_RESULTS_DIR} "
            f"for a smoke run"
        )

    written = write_report(report, path)
    print(f"wrote {written}")
    return report


__all__ = ["build_report", "main", "percentiles", "run", "write_report"]

if __name__ == "__main__":  # pragma: no cover
    main()
