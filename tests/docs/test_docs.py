"""The documents this lane owns have to keep their promises.

A threat model is worth exactly the tests it names, so every `tests/...::test_name` it cites is
checked here against the repository, and every file it points at has to exist. The same goes for
links: a broken relative link in a README is a claim that quietly stopped being true.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
README = REPO / "README.md"
THREAT_MODEL = REPO / "THREAT_MODEL.md"
ADR_DIR = REPO / "docs" / "adr"
WRITEUP = REPO / "docs" / "writeup.md"

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
TEST_REFERENCE = re.compile(r"`(tests/[^`]+?)`")
#: Backticked paths whose existence this lane is asserting. `.json` is deliberately absent:
#: `bench/results/*.json` does not exist until `make bench` runs.
CITED_FILE = re.compile(r"`([\w./-]+\.(?:py|yml|yaml|ts|tsx|css|lock|md))`")
#: A document may cite a file that does not exist yet only by declaring it, in one place, with
#: `<!-- pending: some/path.md, other/path.py -->`. An undeclared missing path is a broken claim.
PENDING = re.compile(r"<!--\s*pending:\s*([^>]+?)\s*-->")


def _pending(text: str) -> set[str]:
    return {item.strip() for group in PENDING.findall(text) for item in group.split(",")}


def _docs() -> list[Path]:
    return [README, THREAT_MODEL, WRITEUP, *sorted(ADR_DIR.glob("*.md"))]


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.name)
def test_every_relative_link_resolves_to_a_file_that_exists(doc: Path) -> None:
    assert doc.exists(), f"{doc.name} is named by this lane and must exist"
    for target in LINK.findall(doc.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#", "<")):
            continue
        path, _, _anchor = target.partition("#")
        if not path:
            continue
        assert (doc.parent / path).exists(), f"{doc.name}: broken link to {target}"


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.name)
def test_every_cited_source_file_exists(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    pending = _pending(text)
    cited = CITED_FILE.findall(text)
    assert cited, f"{doc.name} cites the code it describes"
    for path in cited:
        if path in pending:
            continue
        assert (REPO / path).exists(), f"{doc.name} names a file that does not exist: {path}"


def test_every_test_named_in_the_threat_model_exists() -> None:
    """A mitigation row is worth the test it names, so every name is resolved here."""
    named = TEST_REFERENCE.findall(THREAT_MODEL.read_text(encoding="utf-8"))
    assert len(named) >= 20, f"the STRIDE-lite table names a test per mitigation; found {named}"
    for reference in named:
        path, _, test_name = reference.partition("::")
        target = REPO / path
        assert target.exists(), f"THREAT_MODEL.md names a missing test file: {path}"
        if test_name:
            body = target.read_text(encoding="utf-8")
            assert f"def {test_name}(" in body, f"{path} has no {test_name}"


def test_the_threat_model_has_every_section_prd_17_3_names() -> None:
    text = THREAT_MODEL.read_text(encoding="utf-8").lower()
    for heading in ("## assets", "## trust boundaries", "## stride", "## residual risks"):
        assert heading in text, f"missing section {heading}"
    for subject in (
        "judge injection",
        "ssrf",
        "rebinding",
        "spend abuse",
        "feedback spam",
        "secret leakage",
        "model download tampering",
        "dependency risk",
        "install-time code execution",
    ):
        assert subject in text, f"PRD §17.3 names {subject!r} and the document does not"


def test_the_threat_model_names_every_trust_boundary_prd_17_3_lists() -> None:
    text = THREAT_MODEL.read_text(encoding="utf-8").lower()
    for boundary in ("browser", "api", "worker", "sandbox", "anthropic api", "hub"):
        assert boundary in text, f"the trust-boundary chain is missing {boundary!r}"


def test_the_threat_model_records_the_mcp_server_has_no_daily_ledger() -> None:
    """L8's ruling, and the one place a reader would otherwise assume the API's cap applies."""
    text = THREAT_MODEL.read_text(encoding="utf-8")
    assert "no daily ledger" in text.lower()
    assert "judge_max_calls_per_scan" in text


def test_the_threat_model_lists_the_open_items_the_reviews_deferred() -> None:
    text = THREAT_MODEL.read_text(encoding="utf-8")
    for item in ("ip_hash", "getaddrinfo", "server_instructions", "last_usage", "pip-audit"):
        assert item in text, f"the residual risks do not mention {item}"


def test_the_threat_model_does_not_claim_the_injection_gate_passed() -> None:
    """No API key has ever been used here, so the gate is written and unmeasured (L4)."""
    text = THREAT_MODEL.read_text(encoding="utf-8").lower()
    assert "unmeasured" in text
    assert "never run against a real model" in text


# ----------------------------------------------------------- the decision records (Task 10.3)


def test_the_adr_index_lists_every_adr_and_each_one_exists() -> None:
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    files = sorted(p.name for p in ADR_DIR.glob("0*.md"))
    assert len(files) == 8, f"PRD §7.3 names ADR-001 to ADR-008; found {files}"
    for name in files:
        assert name in index, f"{name} is not listed in docs/adr/README.md"


@pytest.mark.parametrize("adr", sorted(ADR_DIR.glob("0*.md")), ids=lambda p: p.name)
def test_every_adr_keeps_the_four_headings_and_says_who_owns_the_text(adr: Path) -> None:
    text = adr.read_text(encoding="utf-8")
    for heading in ("## Status", "## Context", "## Decision", "## Consequences"):
        assert heading in text, f"{adr.name} is missing {heading}"
    assert "Proposed" in text, f"{adr.name} must still be proposed: the human writes the text"
    assert "human" in text, f"{adr.name} must say the human finalises it (PRD §7.3)"


def test_the_adrs_cover_the_subjects_prd_7_3_names() -> None:
    subjects = {
        "0002": "abstain band",
        "0003": "holdout",
        "0004": "postgres",
        "0005": "onnx",
        "0006": "scanner-agnostic",
        "0007": "glama",
        "0008": "dark-only",
    }
    for number, subject in subjects.items():
        matches = list(ADR_DIR.glob(f"{number}-*.md"))
        assert len(matches) == 1, f"expected exactly one ADR-{number}, found {matches}"
        assert subject in matches[0].read_text(encoding="utf-8").lower(), (
            f"ADR-{number} does not mention {subject!r}"
        )


# ------------------------------------------------------------- the write-up skeleton (10.3)


def _headings(path: Path) -> list[str]:
    return [
        line.lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]


def test_the_writeup_follows_prd_17_2_section_order() -> None:
    order = [
        "audit",
        "scanner sees",
        "taxonomy",
        "corpus",
        "baseline",
        "model",
        "judge",
        "gets wrong",
        "reproduce",
    ]
    headings = _headings(WRITEUP)
    positions = [next((i for i, h in enumerate(headings) if key in h), -1) for key in order]
    assert all(p >= 0 for p in positions), dict(zip(order, positions, strict=True))
    assert positions == sorted(positions), f"sections out of PRD §17.2 order: {headings}"


def test_the_writeup_places_its_figures_in_bench_results_figures() -> None:
    text = WRITEUP.read_text(encoding="utf-8")
    figures = re.findall(r"`(bench/results/figures/[\w.-]+)`", text)
    assert len(figures) >= 5, f"the skeleton reserves a figure slot per section; found {figures}"
    assert len(set(figures)) == len(figures), "two figure slots share a filename"


def test_the_writeup_is_marked_for_the_human_and_states_no_result() -> None:
    text = WRITEUP.read_text(encoding="utf-8")
    assert "<!-- human: rewrite -->" in text
    assert "skeleton" in text.lower()
    # No section may quote a measured figure: nothing has been trained.
    assert "95% CI" not in text
