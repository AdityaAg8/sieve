# SieveBench datasheet

SieveBench is the corpus behind Sieve, a triage layer that sits after an MCP tool-poisoning
scanner and decides which of its findings a human should read. The benchmark is the
artefact; the model is downstream of it.

This sheet describes the **v1** pipeline as built on branch `training-v1` (2026-10-03).
`bench/publish.py` fills the Composition numbers from `bench/results/corpus_stats.json` and
the agreement numbers from `bench/results/agreement.json` when it builds the dataset card.
Nothing in `bench/results/` is ever edited by hand: `make bench` regenerates it. Where v1
departs from the PRD, [ADR-009](../docs/adr/0009-training-v1-deviations.md) says so.

## Motivation

Scanners for MCP tool poisoning fire on directive language. A great deal of directive
language in real tool descriptions is entirely legitimate: ordering constraints, argument
formats, safety caveats. The false positives that follow are what makes scanner output
tiring to read, and tiring output is output that stops being read.

SieveBench exists to measure that boundary rather than assert it. It has three classes —
`benign`, `imperative_benign` (the false-positive class) and `poisoned` — and the decision
rule between the middle and the ends is a single question: *would a reasonable tool author
with no malicious intent write this to make the tool work correctly?*

It was built as a public artefact because a benchmark nobody can check is a claim, not
evidence.

## Composition

<!-- COMPOSITION -->

Counts are crossed by class × tier × concealment × source × split × schema fidelity in
`corpus_stats.json`. Every real row has `schema_fidelity = exact`: its schema is what the
server itself returned from `tools/list` (`bench/collect/catalog.py`).

`source` takes four values (`sieve/types.py`, `CorpusSource`):

- **`public_repo`**: a real tool that the pinned Cisco YARA analyzer does not flag.
- **`wild_fp`**: a real tool that the pinned Cisco YARA analyzer *does* flag
  (`bench/data/catalog/wild_findings.json` records the analyzer version and date). The name
  says what the scanner did, not what the tool is. A `wild_fp` row is a scanner finding whose
  label still comes from labelling (below); "fp" is the expected outcome, not an assumption the
  label inherits.
- **`synthetic_mutation`**: a real host row with one inserted payload.
- **`real_poisoned`**: published research attack rows, pointer-only and eval-only.

Real rows are capped per server: at most 2 `public_repo` and 4 `wild_fp` tools from any one
package (`bench.collect.catalog --per-server-public/--per-server-flagged`), so no single server
dominates.

## Collection process

1. **Source.** The real rows come from `automatelab/mcp-servers-tool-catalog`, pinned at
   revision `a413afb7`. Its authors installed and ran each npm-published MCP server and recorded
   the `tools/list` reply. Sieve reads that published artefact and **runs no server** (SC-1).
   Every real row therefore has `capture_method = "published_artifact"`, `source_repo` = the
   catalog, and `source_commit` = the pinned revision. The catalog's own CC BY 4.0 covers its
   curation and is credited.
2. **Licence.** A row's licence is the **npm `license` field** of its package. It was fetched from
   `https://registry.npmjs.org/<package>/latest` on 2026-09-21 for all 357 packages and recorded,
   with provenance, in `bench/data/catalog/licences.json`. Only SC-6 licences are kept: MIT,
   Apache-2.0, BSD-2-Clause, BSD-3-Clause and ISC. A missing field, a failed lookup, a non-SPDX
   string ("SEE LICENSE IN …") and every copyleft licence drop the package. **MPL-2.0 is
   excluded** (file-level copyleft), and `publish.py` fails if an MPL-2.0 row reaches it.
3. **Clean-up.** Dropped and counted: duplicate names, empty descriptions, invalid schemas, and
   exact text duplicates across forks (one copy kept). Secrets are redacted before anything is
   written to disk (SC-5, `redact_tool`).
4. **Provenance.** Every record carries `source_repo`, `source_commit`, `source_license`,
   `capture_method` and `server_name`, which is the npm package, so `NOTICE` can attribute it.

No registry with redistribution restrictions contributes data, and there is no Glama data
(SC-6).

## Synthesis protocol

Synthetic rows are mutations of real host records: one payload, inserted into a named field,
with `mutation_of` and `payload_fields` recorded so each pair is a controlled comparison.

- **The plan decides everything but the words.** `bench/generate/plan.py` writes
  `plan_v1.json` before any text is generated. Each row in it fixes:
  - the host;
  - the label;
  - the insertion site;
  - the poisoned sub-labels;
  - the **difficulty tier** (`easy` / `medium` / `hard`, drawn 30/40/30);
  - the **concealment**.

  Tiers are assigned by the plan, not by the generator and not by a human at labelling time.
- **Payloads are regenerated locally by two non-Claude families.** Qwen
  (`qwen3.8:27b-2.88bpw-text`) and Gemma (`gemma4:26b-a4b-it-qat`) generate through Ollama on
  loopback, with temperature 0 and a fixed seed (`bench/generate/generate_local.py`). The system
  prompt states the defensive purpose and forbids working code, real credentials, real hosts and
  any domain but `example.invalid`.
  - A register lint rejects a payload that breaks the length rule, contains a banned phrase, or
    uses words from the gate's tightening list. A rejected payload is asked for again; after three
    failures the row is dropped and counted.
  - v1 planned 542 payloads and dropped 4.
- **Hand-authored share.** The hand-written rows (`generator_model = "human"`, `sb-hum-*`) are the
  v0 rows. They are **6.1%** of the synthetic rows, below the PRD's 25% target (ADR-009).
- **No detection rules in the prompt.** The generation prompt is never shown a detection rule or
  a rule name. A generator shown the rules writes payloads that trip the rules, and any
  false-positive reduction measured against those rules would then be circular.
- **Concealment** is applied to poisoned rows only (`bench/generate/conceal.py`), drawn as 72%
  `none` and 7% each of four mechanisms:
  - `html_comment`: the payload inside `<!-- … -->`, invisible when rendered;
  - `zero_width`: zero-width spaces every three characters, readable but defeating naive
    matching;
  - `bidi`: wrapped in a right-to-left override, so it renders misleadingly in terminals;
  - `whitespace`: pushed below twelve blank lines, out of the first screen.

  Concealment is orthogonal to the tier. A hidden payload can be `easy`.
- **Synthetic `benign` and `imperative_benign` rows exist on purpose.** Without them `source`
  would be collinear with `label`, and the leakage probe below could not mean anything.

The `real_poisoned` slice is drawn only from already-published research artefacts (MCPTox,
pinned at `f85189f9`). It is **eval-only**: never trained on, never used for any decision,
opened once by `bench/eval_final.py`, and reported whatever the number is. MCPTox has no licence,
so its text is never committed. A pointer and a SHA-256 are kept instead. Server names in that
slice are hashed; the citation is not.

## Labelling protocol

Labels are split into two binary decisions, because every headline metric is binary
(poisoned vs not). Human time goes where it changes a published number (training plan §4.1).

- **Real rows: an ensemble of three.**
  - The votes are the Sonnet provisional label, Qwen and Gemma (`bench/label/ensemble.py`,
    `bench/label/vote.py`), under a frozen prompt (`bench/label/prompts/ensemble_v1.md`).
  - The tool text is framed as untrusted data and secrets are redacted.
  - The reply must match a schema; a reply that fails it three times is a counted parse failure.
  - `benign` vs `imperative_benign` is the majority of the three. **Any** poisoned vote sends the
    row to the human review queue.
- **Synthetic rows: the construction label,** checked by the family that did *not* write the
  payload (hand-written rows by both). A disputed row is regenerated once; if it is still
  disputed, it is dropped and counted.
- **Humans.** Three blind sessions, against `LABELLING_GUIDE.md`. Each hides the label, id and
  provenance, and presents rows in a seeded order.
  - `make label-dev-audit`: 50 seeded dev rows, gating training at κ ≥ 0.70 against the
    ensemble.
  - `make label-test-id`: the real test-ID rows, gold and report-only.
  - `make review-queue`.
- **What is reported:**
  - human-vs-ensemble κ on the sealed test-ID sample, with a CI;
  - the ensemble error rate with Clopper–Pearson and server-cluster bootstrap CIs;
  - pairwise κ between the three labellers;
  - **ensemble-vs-generator agreement** on synthetic rows. This is a property of the
    generators, not an inter-rater statistic, and it is never reported as κ.

  There is no second human annotator and no intra-rater retest. PRD M8 is not met and is
  replaced as above (ADR-009).

<!-- AGREEMENT -->

## Source-leakage probe

`make probes` runs four probes on dev rows (`bench/analysis/probes.py`):

- source leakage *within* each of the imperative and benign strata;
- a surface-feature probe;
- a length-only probe;
- a transfer probe.

Each is a TF-IDF + logistic-regression (or length) model predicting real vs synthetic on a
grouped split, and its pooled out-of-fold AUROC is published. Gate: **every probe ≤ 0.70**.

The within-class restriction is the whole point. Run over the entire corpus the probe simply
relearns the label, scores high for an uninteresting reason, and can never fail its own gate.
Within one class the label is constant, so whatever the probe finds is style, which is what the
gate is about. The v0 corpus scored 0.929, which is why every payload was regenerated. `make gate`
is the pre-registered check, with one tightening pass; its v1 value is in
`bench/results/gate.json`.

<!-- LEAKAGE -->

Deduplication runs over the whole mutation graph (`bench/split/dedup.py`):

- a pair is a near twin when max(cosine of the full serialisation, cosine of the description
  alone) is at least 0.92, or when its NFKC-normalised text matches exactly. Parameters alone are
  never a twin signal: short shared schemas would pair thousands of unrelated tools;
- near-twin components are kept in one dev fold;
- dev copies of exact cross-split duplicates are dropped;
- the sealed split is untouched;
- dropping a host drops its mutations.

Every evaluation set is also deduplicated against dev (`make dedup-cross`), with exact and near
twins counted separately in `bench/results/dedup_cross.json`.

## LLM annotator

Two of the three ensemble labellers, Qwen and Gemma, are also the two payload generators (never
on the same row). The ensemble-vs-generator agreement is reported here for completeness:
the same model family authored the synthetic payloads; this figure supports no claim.

It never appears in the README. The stage-2 LLM judge is deferred to v1.1, so v1 publishes no
judge agreement.

## Limitations

- **Poisoned training samples are synthetic.** The only real poisoned text is the eval-only
  `real_poisoned` slice and the report-only val-OOD sets. Generalisation to real attacks is
  evidenced by recall there, not by the leakage probe.
- **Labels are ensemble labels.** The human audits measure them; they do not replace them, except
  on the dev-50 rows.
- **The licence is the publisher's own claim.** It is the npm `license` field, as declared by
  whoever published the package, not checked against the repository's `LICENSE` file. A package
  that declares MIT on npm but ships a different `LICENSE` is recorded as MIT. That is the
  licence-provenance caveat; a takedown request (below) is the remedy.
- **Coverage is skewed.** It follows the npm ecosystem and the permissive-licence filter, so
  Python-only servers and copyleft ecosystems are under-represented.
- **Only servers that ran are included.** The catalog includes only servers its authors could
  install and run, and that exclusion is not random with respect to how a server is built.

<!-- TEST_OPENED -->

## Licensing

**Two licences, deliberately.**

- The annotations, the taxonomy, the splits and the schema are licensed **CC BY 4.0**.
- Each record's `tool`, `server_instructions` and `sibling_tool_names` fields stay under that
  record's own `source_license`, which is recorded per record.

The `NOTICE` file lists every source, revision and licence, and reproduces the attributions
Apache-2.0 requires. Eval-only sets with no licence (MCPTox) are never redistributed. Sealed
splits are not published until the first line of `bench/results/test_opened.log` exists
(`bench/publish.py`).

Relicensing someone else's tool descriptions under CC BY would have been a licensing error,
which is why the corpus carries two licences rather than one.

**Takedown.** Write to the contact in the dataset card. Any record will be removed on a
maintainer's request within **7 days**, and the dataset version re-tagged.

## Citation

```bibtex
@misc{sievebench,
  title  = {SieveBench: a labelled corpus for triaging MCP tool-poisoning findings},
  author = {Agarwal, Aditya},
  year   = {2026},
  note   = {Annotations CC BY 4.0; source text under each record's own licence}
}
```
