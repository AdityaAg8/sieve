# ADR-007: No Glama data, and a two-licence split for the corpus

## Status

Proposed — drafted from PRD SC-6, §9.4 and §9.8 by an agent in Phase 3, against `bench/publish.py`
as built. The human finalises this ADR's text.

## Context

SieveBench is a corpus of real tool definitions taken from other people's repositories. Two
questions decide whether it can be published at all: where each row came from, and whether that
source's licence permits redistributing the text.

Aggregator APIs make the first question easy and the second one unanswerable. Glama in particular
indexes MCP servers and would have been the fastest way to a large corpus, but its terms restrict
redistribution and its records are a pointer to someone else's text rather than a licence to
republish it. A corpus that cannot be redistributed cannot be a benchmark.

## Decision

- **No Glama data, and no data from any registry API with redistribution restrictions** (SC-6).
  The official MCP registry is used for *discovery* only; the licence for each row is fetched from
  the repository itself (`GET /repos/{owner}/{repo}/license`), because registry entries carry no
  SPDX identifier at all.
- **Every corpus record carries `source_repo`, `source_commit`, `source_license` and
  `capture_method`.** A row missing any of them fails publication.
- **Publication is an allow-list**, not a deny-list: MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause
  and ISC. **MPL-2.0 is excluded** despite appearing on an early draft list, because it is
  file-level copyleft and SC-6 excludes it. A pointer-derived row is refused whatever its licence
  string says.
- **The corpus ships under two licences**: the annotations, taxonomy, splits and schema are CC
  BY 4.0; each row's quoted text stays under that row's own source licence, recorded per record,
  with an Apache-2.0 attribution notice generated when any Apache-2.0 row is present. The datasheet
  carries a takedown commitment.

## Consequences

- The corpus is smaller and slower to build. Discovery, licence fetch, exclusion logging and a
  capture that may fail all sit between a registry entry and a usable row.
- The gate is mechanical rather than aspirational: `tests/bench/test_publish.py` plants an MPL-2.0
  row, a provenance-less row and a pointer-derived row, and each makes publication exit non-zero.
  An allow-list is used precisely because a deny-list of one entry passes everything it has never
  heard of.
- Some servers cannot be included at all (no licence, `NOASSERTION`, or an install that needs
  lifecycle scripts). Exclusions are recorded with a reason and counted in `corpus_stats.json`,
  so the corpus's own shape is auditable instead of being the residue of what happened to work.
- The two-licence split has to be explained every time the dataset is cited, which is why it is in
  the datasheet and in the generated dataset card rather than in a README sentence.
