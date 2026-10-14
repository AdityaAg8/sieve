"""The public documents have to keep their promises.

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
ARCHITECTURE = REPO / "docs" / "ARCHITECTURE.md"

LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
TEST_REFERENCE = re.compile(r"`(tests/[^`]+?)`")
#: Backticked paths whose existence the documents assert. `.json` is deliberately absent:
#: `bench/results/*.json` does not exist until `make bench` runs.
CITED_FILE = re.compile(r"`([\w./-]+\.(?:py|yml|yaml|ts|tsx|css|lock|md))`")
#: A document may cite a file that does not exist yet only by declaring it, in one place, with
#: `<!-- pending: some/path.md, other/path.py -->`. An undeclared missing path is a broken claim.
PENDING = re.compile(r"<!--\s*pending:\s*([^>]+?)\s*-->")


def _pending(text: str) -> set[str]:
    return {item.strip() for group in PENDING.findall(text) for item in group.split(",")}


def _docs() -> list[Path]:
    return [README, THREAT_MODEL, ARCHITECTURE, *sorted(ADR_DIR.glob("*.md"))]


@pytest.mark.parametrize("doc", _docs(), ids=lambda p: p.name)
def test_every_relative_link_resolves_to_a_file_that_exists(doc: Path) -> None:
    assert doc.exists(), f"{doc.name} must exist"
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


def test_the_stride_table_is_one_contiguous_block_of_numbered_rows() -> None:
    """GFM ends a table at the first blank line: an orphaned row renders as literal text."""
    lines = THREAT_MODEL.read_text(encoding="utf-8").split("\n")
    header = next(i for i, line in enumerate(lines) if line.startswith("| # | Asset | Threat |"))
    assert lines[header + 1].startswith("|---"), "the header separator follows the header"

    body: list[str] = []
    for line in lines[header + 2 :]:
        if not line.startswith("|"):
            break
        body.append(line)
    numbers = [int(line.split("|")[1].strip()) for line in body]
    assert numbers == list(range(1, len(numbers) + 1)), f"rows are not 1..n in order: {numbers}"
    # The count is a tripwire for a row appended without a test beside it;
    # `test_every_test_named_in_the_threat_model_exists` above is what resolves the names.
    assert len(numbers) == 30, f"the STRIDE-lite table has {len(numbers)} rows, expected 30"

    # And nothing that looks like a row may sit outside the block it belongs to.
    rows_in_file = [line for line in lines if re.match(r"^\| \d+ \| A", line)]
    assert len(rows_in_file) == len(body), "a numbered row is stranded outside the table"


def test_the_threat_model_has_every_expected_section() -> None:
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
        assert subject in text, f"the threat model does not mention {subject!r}"


def test_the_threat_model_names_every_trust_boundary() -> None:
    text = THREAT_MODEL.read_text(encoding="utf-8").lower()
    for boundary in ("browser", "api", "worker", "sandbox", "anthropic api", "hub"):
        assert boundary in text, f"the trust-boundary chain is missing {boundary!r}"


def test_the_threat_model_records_the_mcp_server_has_no_daily_ledger() -> None:
    """The one place a reader would otherwise assume the API's cap applies."""
    text = THREAT_MODEL.read_text(encoding="utf-8")
    assert "no daily ledger" in text.lower()
    assert "judge_max_calls_per_scan" in text


def test_the_threat_model_lists_the_open_items_left_unfixed() -> None:
    text = THREAT_MODEL.read_text(encoding="utf-8")
    for item in ("ip_hash", "getaddrinfo", "server_instructions", "last_usage", "pip-audit"):
        assert item in text, f"the residual risks do not mention {item}"


def test_the_threat_model_does_not_claim_the_injection_gate_passed() -> None:
    """No API key has been used for it, so the gate is written and unmeasured."""
    text = THREAT_MODEL.read_text(encoding="utf-8").lower()
    assert "unmeasured" in text
    assert "never run against a real model" in text


# ------------------------------------------------------------------ the decision records


def test_the_adr_index_lists_every_adr_and_each_one_exists() -> None:
    index = (ADR_DIR / "README.md").read_text(encoding="utf-8")
    files = sorted(p.name for p in ADR_DIR.glob("0*.md"))
    assert len(files) == 9, f"expected ADR-001 to ADR-009; found {files}"
    for name in files:
        assert name in index, f"{name} is not listed in docs/adr/README.md"


@pytest.mark.parametrize("adr", sorted(ADR_DIR.glob("0*.md")), ids=lambda p: p.name)
def test_every_adr_keeps_the_four_headings_and_is_accepted(adr: Path) -> None:
    text = adr.read_text(encoding="utf-8")
    for heading in ("## Status", "## Context", "## Decision", "## Consequences"):
        assert heading in text, f"{adr.name} is missing {heading}"
    assert "Accepted" in text, f"{adr.name} must state its status"


def test_the_adrs_cover_the_subjects_they_are_about() -> None:
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
