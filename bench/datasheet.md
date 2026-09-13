# SieveBench datasheet

SieveBench is the corpus behind Sieve, a triage layer that sits after an MCP tool-poisoning
scanner and decides which of its findings a human should read. The benchmark is the
artefact; the model is downstream of it.

`bench/publish.py` fills the Composition numbers from `bench/results/corpus_stats.json` and
the agreement numbers from `bench/results/agreement.json` when it builds the dataset card.
Nothing in `bench/results/` is ever edited by hand — `make bench` regenerates it.

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
`corpus_stats.json`. `schema_fidelity` is `exact` where the tool's own JSON Schema was
recovered and `approx` where it was reconstructed from type annotations or a zod shape.

## Collection process

1. **Discovery** — the official MCP registry (`registry.modelcontextprotocol.io`,
   `GET /v0.1/servers`), used for discovery only. No registry with redistribution
   restrictions contributes data.
2. **Licence** — the registry carries no SPDX id, so each candidate's licence comes from its
   repository's own `LICENSE` via the GitHub API. Only MIT, Apache-2.0, BSD-2-Clause,
   BSD-3-Clause and ISC are kept. **MPL-2.0 is excluded** (file-level copyleft), and
   `publish.py` fails if an MPL-2.0 row reaches it.
3. **Capture** — static extraction first (`bench/collect/static_py.py` walks the Python AST,
   `bench/collect/static_ts.py` the TypeScript one), because reading a stdio server's tools
   by running it executes the server. Where static capture fails or would only yield an
   approximate schema, `bench/collect/sandbox_capture.py` installs *and* runs the server
   inside one disposable container with lifecycle scripts disabled and no network at the
   capture stage. A server that cannot be installed that way is excluded and counted.
4. **Provenance** — every record carries `source_repo`, `source_commit`, `source_license`
   and `capture_method`.

## Synthesis protocol

Synthetic rows are mutations of real host records: one payload, inserted into a named field,
with `mutation_of` and `payload_fields` recorded so each pair is a controlled comparison.

- Payloads are either generated under a system prompt that states the defensive purpose and
  forbids working code, real credentials, real hosts and any domain but `example.invalid`,
  or hand-authored. **At least 25% of poisoned payloads are hand-authored**
  (`generator_model = "human"`); the actual share is published above.
- The generation prompt is never shown a detection rule or a rule name. A generator shown
  the rules writes payloads that trip the rules, and any false-positive reduction measured
  against those rules would then be circular.
- Difficulty tiers are **semantic** and assigned by the human at labelling time, not by the
  generator. Concealment (`none`, `html_comment`, `zero_width`, `bidi`, `whitespace`) is an
  orthogonal attribute, crossed with tier and reported per mechanism.
- Synthetic `benign` paraphrases exist on purpose: without them `source` would be collinear
  with `label` and the leakage probe below could not mean anything.

The `real_poisoned` slice is drawn only from already-published research artefacts and
disclosures. It is **eval-only**: never trained on, never used for any decision, opened once
by `bench/eval_final.py` and reported whatever the number is. Server names in that slice are
hashed; the citation is not.

## Labelling protocol

Every row was labelled by one person against `LABELLING_GUIDE.md`. Synthetic rows were
labelled **blind**: the session hides the label, the sub-labels, the tier, the concealment,
the payload fields, the generator and the host, because seeing the generator's intent
anchors the reader exactly as seeing the label would.

Agreement is reported three ways, plus a fourth number that is not agreement:

- **Overall κ** across the sampled subset.
- **κ on the real subset** (`public_repo` and `wild_fp` rows) — the headline, because that
  is where the `benign` ↔ `imperative_benign` boundary actually lives.
- **Per class**: observed agreement plus Gwet's AC1, which does not collapse when a class is
  rare.
- **Blind-vs-generator agreement**: how often the human's blind label matched the
  generator's recorded intent. This is a property of the generator, not an inter-rater
  statistic, and it is never reported as κ.

The inter-rater subset is a **simple random** sample; the stratified version is reported
second, because a stratified subset distorts prevalence and κ is prevalence-sensitive.
Intra-rater (test-retest) agreement uses a fresh random 15% re-labelled after at least seven
days.

<!-- AGREEMENT -->

## Source-leakage probe

Within the `imperative_benign` class only, a TF-IDF + logistic-regression probe predicts
real vs synthetic on a grouped split, and its pooled out-of-fold AUROC is published. Gate:
**AUROC ≤ 0.70**.

The restriction is the whole point. Run over the entire corpus the probe simply relearns the
label, scores high for an uninteresting reason, and can never fail its own gate. Within one
class the label is constant, so whatever the probe finds is style — which is what the gate
is about.

<!-- LEAKAGE -->

Deduplication is stratum-local by design, so a near-duplicate can survive with one member in
the development split and the other in the sealed evaluation split. That count is published
above rather than removed: dropping the pairs would break the mutation graph, and leaving
them unmentioned would overstate what the sealed split measures.

## LLM annotator

The stage-2 judge's agreement with gold is reported here for completeness:
the same model family authored the synthetic payloads; this figure supports no claim.

It never appears in the README.

## Limitations

- Poisoned training samples are **synthetic**. The only real poisoned text in the corpus is
  the eval-only `real_poisoned` slice, and generalisation to real attacks is evidenced by
  the recall on that slice, not by the leakage probe.
- One annotator labelled everything; a second annotator covered a random subset only.
- `approx` schema fidelity means a reconstructed schema, which can differ from what the
  server actually advertises at runtime.
- Coverage follows the permissive-licence filter, so ecosystems that publish under
  copyleft licences are under-represented.
- Servers that could not be installed with lifecycle scripts disabled are absent, and that
  exclusion is not random with respect to how a server is built.

<!-- TEST_OPENED -->

## Licensing

**Two licences, deliberately.** The annotations, the taxonomy, the splits and the schema are
licensed **CC BY 4.0**. Each record's `tool`, `server_instructions` and `sibling_tool_names`
fields remain under that record's own `source_license`, which is recorded per record. The
`NOTICE` file lists every source repository, commit and licence, and reproduces the
attributions Apache-2.0 requires.

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
