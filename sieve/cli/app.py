"""Typer wiring for `sieve` (PRD §15.1). Orchestration only -- ingestion is `sieve.cli.ingest`,
output is `sieve.cli.render`, network egress is `sieve.cli.net`.

Exit codes: `0` clean, `1` findings matched `--fail-on`, `2` input error, `3` model or network
error. Every path below that can fail picks exactly one of these, never a bare traceback.
"""

import dataclasses
import io
import subprocess  # noqa: S404 - one fixed, non-shell invocation (`make bench`)
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

import numpy as np
import numpy.typing as npt
import typer
from pydantic import ValidationError
from rich.console import Console

from sieve import __version__
from sieve.adapters.base import AdapterSchemaError, AdapterVersionUnpinned
from sieve.api.deps import get_judge
from sieve.cli import ingest, net, render
from sieve.config import settings
from sieve.model.infer import load_classifier
from sieve.model.thresholds import ThresholdsMissing
from sieve.pipeline import PipelineDeps, triage_many
from sieve.protocols import Classifier, Thresholds
from sieve.types import ScannerFinding, SourceType, ToolRecord, TriageResult

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_INPUT = 2
EXIT_MODEL = 3

FORMAT_CHOICES = ("pretty", "json", "sarif")
FAIL_ON_CHOICES = ("confirmed", "review", "any", "never")
FROM_CHOICES = ("cisco-yara", "sarif")

app = typer.Typer(add_completion=False, no_args_is_help=True)
bench_app = typer.Typer(add_completion=False, help="Reproduce published bench numbers.")
app.add_typer(bench_app, name="bench")


def _error(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=EXIT_INPUT)


def _fatal(message: str, code: int) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(code=code)


class _OverriddenClassifier:
    """Wraps a real classifier with dev-supplied thresholds (`--threshold-override`).

    Thresholds normally come only from the artefact (PRD §11.4) -- this exists purely to let a
    developer poke at routing behaviour locally, and it says so, loudly, every time it runs.
    """

    def __init__(self, inner: Classifier, thresholds: Thresholds) -> None:
        self._inner = inner
        self.model_version = inner.model_version
        self.thresholds = thresholds

    def predict_proba(self, texts: Sequence[str]) -> npt.NDArray[np.float64]:
        return self._inner.predict_proba(texts)


def _apply_threshold_override(classifier: Classifier, raw: str) -> Classifier:
    parts = raw.split(",")
    if len(parts) != 2:
        _error(f'--threshold-override must be "t_low,t_high", got {raw!r}')
    try:
        t_low, t_high = float(parts[0]), float(parts[1])
    except ValueError:
        _error(f'--threshold-override must be "t_low,t_high", got {raw!r}')
    typer.echo(
        "warning: --threshold-override is a dev-only flag; published thresholds "
        f"(t_low={classifier.thresholds.t_low}, t_high={classifier.thresholds.t_high}) are "
        f"overridden with t_low={t_low}, t_high={t_high} for this run only",
        err=True,
    )
    thresholds = dataclasses.replace(classifier.thresholds, t_low=t_low, t_high=t_high)
    return _OverriddenClassifier(classifier, thresholds)


def _ingest(
    input_arg: str | None,
    from_: str | None,
    config: Path | None,
    static: bool,
) -> tuple[list[ToolRecord], dict[str, list[ScannerFinding]], SourceType, str | None]:
    if config is not None and input_arg is not None:
        _error("provide either INPUT or --config PATH, not both")
    if static and config is None:
        _error("--static requires --config PATH")
    if config is not None and not static:
        _error("--config requires --static (Sieve never spawns an MCP server)")
    if config is None and input_arg is None:
        _error("provide INPUT (a file path or '-') or --config PATH --static")
    if config is not None and from_ is not None:
        _error("--from is not meaningful with --config --static (no tools are parsed)")

    try:
        if config is not None:
            text = ingest.read_text(str(config))
            doc = ingest.load_json(text, what=str(config))
            ingest.parse_static_config(doc)  # validates the shape; parse-only (SC-1)
            # Only what the config declares for itself: SC-1 forbids starting a server to ask
            # it, so a config that declares no `tools` array triages nothing (PRD §15.2).
            return ingest.declared_tools(doc), {}, "mcp_config", str(config)

        assert input_arg is not None  # the mutual-exclusion checks above guarantee this
        text = ingest.read_text(input_arg)
        doc = ingest.load_json(text, what=input_arg)
        source_path = None if input_arg == "-" else input_arg
        if from_ is not None:
            records, findings_by_id = ingest.load_from_scanner(from_, doc)  # type: ignore[arg-type]
            return records, findings_by_id, "scanner_report", source_path
        return ingest.load_tools(doc), {}, "tools_json", source_path
    except ingest.IngestError as exc:
        _error(str(exc))
    except (AdapterSchemaError, AdapterVersionUnpinned) as exc:
        _error(str(exc))
    except ValidationError as exc:
        _error(f"input failed validation: {exc}")


def _run_locally(
    records: list[ToolRecord],
    findings_by_id: dict[str, list[ScannerFinding]],
    *,
    model: str,
    no_judge: bool,
    offline: bool,
    threshold_override: str | None,
) -> list[TriageResult]:
    cache_dir = Path(settings.model_cache).expanduser()
    classifier: Classifier
    if offline:
        classifier = net.load_classifier_offline(model, revision=None, cache_dir=cache_dir)
    else:
        classifier = load_classifier(model, revision=None, use_onnx=True, cache_dir=cache_dir)
    if threshold_override is not None:
        classifier = _apply_threshold_override(classifier, threshold_override)

    judge = None if (no_judge or offline) else get_judge()
    deps = PipelineDeps(classifier=classifier, judge=judge, judge_enabled=judge is not None)
    return triage_many(
        records, findings_by_id, deps, judge_budget=settings.judge_max_calls_per_scan
    )


def _run_via_api(records: list[ToolRecord], api_url: str, *, offline: bool) -> list[TriageResult]:
    if offline:
        # No local model to download here -- API mode has no classifier of its own -- but
        # `--offline` still means "no network", so the guard goes up immediately and the very
        # first request below is what trips it.
        net.install_guard()
    with net.TriageClient(api_url) as client:
        return [client.triage_tool(record) for record in records]


def _resolve_exit_code(results: list[TriageResult], fail_on: str) -> int:
    """PRD §15.1's four tiers, an ordered scale by Sieve verdict severity plus one wider tier
    (ruling, fix round 1): `confirmed` ⊂ `review` (adds `needs_review`) ⊂ `any` (every tool the
    *scanner* flagged, regardless of what Sieve concluded about it -- so `any` can still fire on
    a run where Sieve dismissed every single tool: the scanner's original call is not Sieve's to
    overrule for this gate). `any` uses the same quantity as `render.py::build_scan_summary`'s
    `scanner_flagged` count: `bool(r.scanner_findings)`.
    """
    if fail_on == "never":
        return EXIT_OK
    if fail_on == "confirmed":
        matched = any(r.verdict == "confirmed" for r in results)
    elif fail_on == "review":
        matched = any(r.verdict in ("confirmed", "needs_review") for r in results)
    else:
        matched = any(r.scanner_findings for r in results)
    return EXIT_FINDINGS if matched else EXIT_OK


def _write_output(text: str, output: Path | None) -> None:
    if output is not None:
        output.write_text(text, encoding="utf-8", newline="\n")
    else:
        typer.echo(text, nl=False)


@app.command()
def triage(
    input: str | None = typer.Argument(
        None, metavar="[INPUT]", help="A tools/list JSON file, or '-' for stdin."
    ),
    from_: str | None = typer.Option(
        None, "--from", help="Ingest a scanner's own report instead: cisco-yara|sarif."
    ),
    config: Path | None = typer.Option(  # noqa: B008 - Typer's own documented pattern
        None, "--config", help="An mcp.json config to parse (requires --static)."
    ),
    static: bool = typer.Option(
        False, "--static", help="Parse --config only; never spawns or connects."
    ),
    format: str | None = typer.Option(None, "--format", help="pretty|json|sarif"),
    output: Path | None = typer.Option(  # noqa: B008 - Typer's own documented pattern
        None, "--output", help="Write to a file, not stdout."
    ),
    fail_on: str | None = typer.Option(None, "--fail-on", help="confirmed|review|any|never"),
    ci: bool = typer.Option(False, "--ci", help="Alias for --format json --fail-on confirmed."),
    no_judge: bool = typer.Option(False, "--no-judge", help="Never call the judge."),
    offline: bool = typer.Option(
        False, "--offline", help="No judge, no API; one permitted model download, then no network."
    ),
    model: str = typer.Option(settings.model_id, "--model", help="A model id, Hub repo, or dir."),
    api_url: str | None = typer.Option(
        None, "--api-url", help="Triage via POST /api/v1/triage instead of running locally."
    ),
    threshold_override: str | None = typer.Option(
        None, "--threshold-override", help='Dev-only: "t_low,t_high" (prints a warning).'
    ),
) -> None:
    """Triage an MCP server's tools for prompt-injection and tool-poisoning risk."""
    if ci:
        if (format is not None and format != "json") or (
            fail_on is not None and fail_on != "confirmed"
        ):
            _error(
                "--ci conflicts with --format/--fail-on (it is --format json --fail-on confirmed)"
            )
        format = "json"
        fail_on = "confirmed"
    format = format or "pretty"
    fail_on = fail_on or "confirmed"

    if format not in FORMAT_CHOICES:
        _error(f"--format must be one of {list(FORMAT_CHOICES)}, got {format!r}")
    if fail_on not in FAIL_ON_CHOICES:
        _error(f"--fail-on must be one of {list(FAIL_ON_CHOICES)}, got {fail_on!r}")
    if from_ is not None and from_ not in FROM_CHOICES:
        _error(f"--from must be one of {list(FROM_CHOICES)}, got {from_!r}")

    records, findings_by_id, source_type, source_path = _ingest(input, from_, config, static)

    if not records:
        # Nothing to score: skip the classifier/judge/API entirely rather than paying for a
        # model load (or the one `--offline` download) that would classify zero texts. A
        # `--config --static` run over a config that declares no tools (SC-1: nothing is
        # started to enumerate them) hits this on every invocation.
        results: list[TriageResult] = []
    else:
        try:
            if api_url is not None:
                results = _run_via_api(records, api_url, offline=offline)
            else:
                results = _run_locally(
                    records,
                    findings_by_id,
                    model=model,
                    no_judge=no_judge,
                    offline=offline,
                    threshold_override=threshold_override,
                )
        except net.NetworkBlocked as exc:
            _fatal(str(exc), EXIT_MODEL)
        except net.ApiError as exc:
            _fatal(str(exc), EXIT_MODEL)
        except (ThresholdsMissing, FileNotFoundError, OSError) as exc:
            _fatal(f"could not load model {model!r}: {exc}", EXIT_MODEL)

    scan = render.build_scan_summary(records, results, source_type=source_type)
    findings = render.build_findings(records, results, scan_id=scan.id)

    if format == "pretty":
        if output is not None:
            buffer = io.StringIO()
            console = Console(file=buffer, no_color=True, width=120)
            render.render_pretty(findings, tool_count=len(records), console=console)
            output.write_text(buffer.getvalue(), encoding="utf-8", newline="\n")
        else:
            render.render_pretty(findings, tool_count=len(records))
    elif format == "json":
        _write_output(render.render_json(findings), output)
    else:
        _write_output(render.render_sarif(scan, findings, source_path=source_path), output)

    raise typer.Exit(code=_resolve_exit_code(results, fail_on))


@bench_app.command("reproduce")
def bench_reproduce() -> None:
    """Reproduce `bench/results/*` from committed artefacts (`make bench`, PRD M6a).

    Shells out to the repo's own `Makefile` rather than re-deriving its steps here, so the two
    never drift apart: this command is only useful run from a clone anyway (bench data and
    committed artefacts live in the repo, not in the wheel).
    """
    try:
        result = subprocess.run(["make", "bench"], check=False)  # noqa: S607
    except FileNotFoundError:
        _fatal(
            "GNU make was not found on PATH; run the `bench` target's steps directly: "
            "bench.ablations.run, bench.analysis.errors, bench.analysis.judge_agreement, "
            "bench.demo (see the Makefile's `bench` target)",
            EXIT_MODEL,
        )
    if result.returncode != 0:
        _fatal("make bench failed; see the output above", EXIT_MODEL)


@app.command()
def version() -> None:
    """Print the installed Sieve version."""
    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
