# Sieve threat model

Sieve reads text written by people it has never met and hands it to a language model. That is the
whole product, and it is also the whole threat model: every tool description, every input schema,
every MCP config and every scanner report that reaches Sieve is attacker-controlled data (PRD
SC-7). This document says what is worth attacking, where the trust boundaries are, what the code
does about each threat, and which test proves it. Every mitigation row names a test file, and
every name in it was checked against the repository before it was written down.

Status: drafted from the code as built on 2026-09-14, at the end of Phase 3. Sections marked
`<!-- human: rewrite -->` are judgement calls that a person has to own, not facts an agent can
verify. Nothing below is measured: the judge's self-defence gate is written and wired but has
never been run against a real model (L4 hand-off), and where that changes an assessment, the row
says so rather than claiming a result.

## Assets

| # | Asset | Why an attacker wants it |
|---|---|---|
| A1 | The user's agent | A poisoned tool description that Sieve dismisses is an instruction the agent will follow: file reads, exfiltration, calls to other tools. This is the asset the product exists to protect. |
| A2 | Sieve's own verdicts | A verdict is a security decision. Flipping one `confirmed` to `dismissed` is the highest-value attack on the system, and it costs nothing to try: the attacker writes the input. |
| A3 | The Anthropic API key and the judge budget | A key in an environment variable, and a per-day dollar ledger anyone with the public URL can spend. |
| A4 | Submitted tool definitions and configs | User-supplied text that can contain credentials by accident (an API key pasted into a description or a default value). |
| A5 | The corpus, splits and published numbers | A benchmark whose holdout leaked, whose licences are wrong, or whose numbers were hand-edited is worthless and unpublishable. |
| A6 | The database and its history | Scan rows, verdicts, feedback, client IP hashes, the spend ledger. |
| A7 | The host running the worker | Remote scanning dials URLs a user supplies; corpus capture installs third-party packages. Both are code-execution-adjacent by nature. |
| A8 | The model artefact | Thresholds and weights pulled from the Hugging Face Hub at start-up; substituting them changes every verdict at once. |

## Trust boundaries

```
browser  ->  API  ->  Postgres queue  ->  worker  ->  sandbox worker  ->  a remote MCP server
                 \                           \
                  ->  Anthropic API (judge)   ->  model artefact (Hugging Face Hub)

CLI / GitHub Action  ->  the same pipeline, in-process, no database
MCP client (stdio)   ->  the same pipeline, in-process, the user's own key
```

- **Browser to API.** Untrusted. Every request is rate-limited and size-capped; responses carry
  `Content-Security-Policy: default-src 'none'`, `X-Frame-Options: DENY` and `Referrer-Policy:
  no-referrer` (`sieve/api/middleware.py`), and CORS allows exactly one origin (`WEB_ORIGIN`).
  **There is no authentication and there are no sessions or cookies**: every route is public by
  design, which is why the abuse controls here are quotas, a spend cap and a data TTL rather than
  roles, and why there is no CSRF surface (nothing to ride: no ambient credential exists).
- **API to worker.** The queue is a Postgres table with `FOR UPDATE SKIP LOCKED`; jobs carry ids,
  not code. A stale worker's job is reaped and re-claimed rather than lost (`sieve/worker/queue.py`).
- **Worker to sandbox.** The only service permitted to reach a remote MCP server is
  `sandbox-worker`, behind the `remote` compose profile and *two* independent gates,
  `SIEVE_ALLOW_REMOTE_MCP=true` and `SIEVE_SANDBOXED=1` (`docker-compose.yml`,
  `sieve/worker/remote.py`). The API and the default worker pin both gates closed explicitly.
- **Sandbox to the internet.** HTTPS only, port 443 only, every DNS answer checked against the
  blocked-range list, the connection pinned to the one validated literal, no redirects, headers
  stripped, 1 MB cap, 10 s deadline (`sieve/worker/ssrf.py`, `sieve/worker/ssrf_client.py`).
- **Sieve to the Anthropic API.** Tool text crosses this boundary. It is redacted first
  (`sieve/judge/client.py`) and rendered inside a nonce-delimited untrusted block
  (`sieve/judge/render.py`).
- **Anything to the holdout.** `bench/eval_final.py` is the only code in `sieve/` or `bench/` that
  may read a sealed row, and a grep test keeps it that way as the repository grows.

## STRIDE-lite

S poofing, T ampering, R epudiation, I nformation disclosure, D enial of service, E levation of
privilege. One row per threat that the code actually answers; the test column is the evidence.

| # | Asset | Threat | Mitigation as built | Test that proves it |
|---|---|---|---|---|
| 1 | A1, A2 | **T** — a tool description instructs the judge to dismiss it | The analysed tool is rendered inside a block delimited by a fresh 16-hex nonce per call, with the delimiters escaped inside the content, so no payload can close the block or forge a real one | `tests/judge/test_render.py::test_content_cannot_close_the_block`, `tests/judge/test_render.py::test_a_guessed_real_nonce_inside_the_content_is_neutralised` |
| 2 | A2 | **T** — a crafted payload flips a verdict to `dismissed` | Two-sided injection suite, 15 clean and 15+ attack cases, replayed from a judge-level cassette; attacks may never be dismissed and clean cases must still be dismissed, so the gate cannot be passed by refusing everything | `tests/test_judge_injection.py::test_attacks_are_never_dismissed`, `tests/test_judge_injection.py::test_zz_clean_dismissal_rate_meets_the_gate` (**unmeasured today**: no API key, every case skips with the recording command as its reason) |
| 3 | A2 | **T** — Sieve's own tool descriptions drift into scanner bait or are tuned to the classifier | Sieve runs its own three MCP tool definitions and server instructions through the real pipeline; every one must come back `dismissed` at `stage = "classifier"` | `tests/test_self_scan.py::test_sieve_dismisses_its_own_tool_definitions` |
| 4 | A7 | **I/E** — SSRF: a submitted URL resolves to cloud metadata or an internal service | Every A and AAAA answer is checked against the blocked IPv4/IPv6 ranges and the metadata addresses; one blocked answer refuses the whole name; mapped and NAT64 addresses are unwrapped and re-checked | `tests/worker/test_ssrf.py::test_every_blocked_range_is_blocked`, `tests/worker/test_ssrf.py::test_a_private_second_answer_blocks_the_whole_name` |
| 5 | A7 | **T** — DNS rebinding between the check and the connection | The validated literal is pinned at the socket layer by a custom httpcore backend; `Host` and SNI keep the original name, so there is no second resolution to rebind | `tests/worker/test_remote.py::test_the_pinned_backend_dials_the_literal_not_the_name`, `tests/worker/test_remote.py::test_the_host_header_is_the_name_while_the_socket_goes_to_the_ip` |
| 6 | A7 | **E** — a redirect or a huge body turns one allowed fetch into another request | Any 3xx is a hard error rather than a hop; the response body is capped at 1 MB mid-stream and the whole fetch at 10 s | `tests/worker/test_remote.py::test_any_redirect_is_a_hard_error`, `tests/worker/test_remote.py::test_a_body_past_the_cap_aborts_mid_stream` |
| 7 | A3, A4 | **I** — credentials leak outbound with a remote fetch | `Authorization`, `Cookie` and `Proxy-*` are stripped from every outbound request, including a client-level default header | `tests/worker/test_remote.py::test_credential_and_proxy_headers_never_reach_the_server`, `tests/worker/test_remote.py::test_a_client_level_auth_header_is_also_stripped` |
| 8 | A7 | **E** — remote scanning is enabled somewhere it should not be | Two independent gates, both pinned explicitly on the API and the default worker; a `.env` file cannot open the second one; the flag is required *even inside* the sandbox | `tests/worker/test_sandbox.py::test_neither_the_api_nor_the_default_worker_may_reach_a_remote_server`, `tests/worker/test_sandbox.py::test_a_dotenv_cannot_open_the_second_gate` |
| 9 | A7 | **E** — the sandbox is not actually sandboxed | The compose service runs its own image, read-only, no host mount, dropped capabilities, opt-in profile | `tests/worker/test_sandbox.py::test_the_sandbox_is_locked_down`, `tests/worker/test_sandbox.py::test_the_sandbox_is_opt_in` |
| 10 | A4, A6 | **I** — a credential in a submitted tool definition is stored or forwarded | `redact_tool` runs at every writer and every outbound call: before the insert (`sieve/api/store.py`), in the pipeline, in the judge client, in the CLI's API client, in every scanner adapter and in corpus capture | `tests/test_redact.py::test_redact_tool_walks_schema`, `tests/test_redact.py::test_private_key_block`, `tests/cli/test_net.py::test_triage_client_sends_redacted_text_only` |
| 11 | A4 | **I** — tool text leaks into logs | `sieve/logging.py`'s `DropToolContent` filter removes content keys above DEBUG and is installed even on an already-configured root logger | `tests/api/test_middleware.py::test_a_scan_log_line_carries_the_scan_id_and_no_tool_text`, `tests/api/test_middleware.py::test_the_sc5_filter_is_installed_even_on_a_pre_configured_root` |
| 12 | A3 | **D** — spend abuse: someone drains the judge budget | A Postgres-backed daily ledger, re-read at **every** routing decision rather than once per scan; exhaustion routes band tools to `needs_review` with `stage = judge_budget_exhausted` instead of failing open | `tests/api/test_spend.py::test_the_cap_closes_the_gate`, `tests/api/test_spend.py::test_an_exhausted_ledger_routes_a_band_tool_to_needs_review`, `tests/api/test_spend.py::test_fifty_concurrent_charges_are_exact` |
| 13 | A3, A6 | **D** — request flooding | Per-IP per-minute and per-day quotas enforced in one statement, so two concurrent requests cannot both pass the boundary; reads are not limited | `tests/api/test_ratelimit.py::test_the_11th_scan_in_a_minute_is_429`, `tests/api/test_ratelimit.py::test_concurrent_hits_cannot_both_pass_the_boundary` |
| 14 | A6 | **D/I** — feedback spam, and the IPs it would collect | A honeypot field: a filled one returns 204 and stores nothing; a real submission stores a salted SHA-256 of the client IP, never the address | `tests/api/test_middleware.py::test_a_filled_honeypot_is_204_and_stores_nothing`, `tests/api/test_middleware.py::test_real_feedback_is_stored_with_a_hashed_ip`, `tests/api/test_ratelimit.py::test_the_ip_hash_is_salted_and_never_the_raw_ip` |
| 15 | A6 | **D** — an oversized body, or a browser asked to render one | 1 MB body cap answered with 413, and the security headers ride on error responses too | `tests/api/test_middleware.py::test_a_body_over_the_cap_is_413`, `tests/api/test_middleware.py::test_every_response_carries_the_security_headers` |
| 16 | A6 | **I** — data kept longer than it is needed | Scans expire after `SIEVE_SCAN_TTL_DAYS` and are deleted with their children by a self-re-enqueuing purge job, bootstrapped once so the chain has a beginning | `tests/worker/test_purge.py::test_expired_scans_are_deleted_and_children_cascade`, `tests/worker/test_purge.py::test_the_purge_chain_is_bootstrapped_once` |
| 17 | A6 | **R** — a crashed worker silently drops or double-runs a job | Claims are atomic, heartbeats expire, a reaped worker can no longer close or fail its old job, and attempts are counted for backoff | `tests/worker/test_queue.py::test_a_stale_job_is_reaped_and_can_be_reclaimed`, `tests/worker/test_queue.py::test_a_reaped_worker_can_no_longer_close_its_old_job` |
| 18 | A3 | **D** — a third-party scanner API is called more than the licence allows | `snyk_agent_scan` counts invocations in a persisted, file-locked counter and refuses at 25 without calling the runner; concurrent scans at the boundary yield exactly one invocation | `tests/adapters/test_snyk_cap.py::test_cap_exceeded_at_25_never_calls_the_runner`, `tests/adapters/test_snyk_cap.py::test_concurrent_scans_at_the_cap_boundary_yield_exactly_one_invocation` |
| 19 | A5 | **T** — the sealed holdout is opened early, or twice, or quietly | `bench/eval_final.py` refuses without `SIEVE_FINAL=1`, appends one line to an append-only log *before* it reads anything, and a grep test asserts no other file in `sieve/` or `bench/` reads a sealed row | `tests/bench/test_eval_final.py::test_refuses_without_the_env_var`, `tests/bench/test_eval_final.py::test_nothing_else_in_the_repository_opens_the_holdout`, `tests/bench/test_eval_final.py::test_the_log_is_opened_in_append_mode_and_never_truncated` |
| 20 | A5 | **R** — a published corpus row cannot be redistributed | Publication is an allow-list of permissive licences, not a deny-list; MPL-2.0, a missing provenance field and a pointer-derived row each exit non-zero | `tests/bench/test_publish.py::test_any_licence_outside_the_permissive_set_is_refused`, `tests/bench/test_publish.py::test_a_planted_mpl_record_makes_publish_exit_non_zero` |
| 21 | A7 | **E** — install-time code execution while capturing a corpus server | Capture builds a disposable image whose install step disables lifecycle scripts (`npm install --ignore-scripts`; `pip install --no-build-isolation --only-binary :all:`), runs it with no host mount and no credential, and only trusts a candidate that has both provenance *and* an install script a human read | `tests/bench/test_sandbox_capture.py::test_build_args_disable_lifecycle_scripts_and_carry_no_credentials`, `tests/bench/test_sandbox_capture.py::test_trust_requires_both_provenance_and_a_read_install_script` |
| 22 | A8 | **T** — the classifier loads an artefact with no calibrated thresholds | `load_classifier` raises rather than defaulting: a null threshold or an uncalibrated artefact is a refusal, never a silent 0.5 | `tests/model/test_infer.py::test_load_classifier_raises_on_a_null_threshold`, `tests/model/test_infer.py::test_load_classifier_raises_when_the_artefact_was_never_calibrated` |
| 23 | A1 | **D** — the CLI phones home from an air-gapped CI runner | `--offline` installs a socket guard that raises on any `connect()`, and the model cache is read local-first so a populated cache opens no socket at all | `tests/cli/test_net.py::test_install_guard_blocks_socket_connect`, `tests/cli/test_net.py::test_ensure_model_cached_hits_cache_first_with_no_network` |
| 24 | A1, A7 | **E** — the GitHub Action or the CLI spawns the MCP server it is asked to scan | `--config PATH --static` parses the config and nothing else; listing a server's tools requires running it, so that mode reports zero tools by construction (SC-1) | `tests/cli/test_triage.py::test_config_static_never_produces_a_tool_and_prints_the_clean_scan_message`, `tests/cli/test_triage.py::test_config_static_never_loads_a_classifier`, `tests/cli/test_action_yml.py::test_self_scan_workflow_parses_and_uses_the_local_action` |
| 25 | A1 | **D** — a hostile MCP client hangs or floods the Sieve MCP server | `scan_tools_list` refuses more than `max_tools_per_scan` tools; the paged finding walk has a page cap and a repeated-cursor check; a redirect from the configured API is an error, not a hop | `tests/mcp/test_server.py::test_scan_tools_list_refuses_more_tools_than_the_cap`, `tests/mcp/test_server.py::test_explain_verdict_treats_a_redirect_as_a_transport_error` |
| 26 | A2, A6 | **T** — malformed input reaches storage, or turns into a 500 | Every request body is a Pydantic v2 model and the OpenAPI schema is frozen and diffed in CI; an unsupported source type is 422, a malformed cursor is 400 rather than 500, and more tools than the cap is 413 | `tests/api/test_scans.py::test_a_source_type_without_its_payload_is_422`, `tests/api/test_scans.py::test_a_malformed_cursor_is_400_not_500`, `tests/test_api_contract.py::test_committed_openapi_matches_live` |
| 27 | A6 | **T** — SQL injection | Every query is typed SQLAlchemy; the two raw statements (the rate-limit upsert and the spend charge) use bound parameters and no string concatenation. The guarantee is structural, so the tests below exercise those two statements rather than proving the absence of a pattern | `tests/api/test_ratelimit.py::test_the_count_survives_a_new_session`, `tests/api/test_spend.py::test_fifty_concurrent_charges_are_exact` |

## The mitigations PRD §17.3 names

### Judge injection

Everything inside a tool definition is data. `sieve/judge/render.py` wraps it in a block delimited
by a nonce generated per call, escapes any occurrence of the delimiter inside the content, and
labels each field (`[NAME]`, `[DESC]`, `[PARAM]`, `[ANNOT]`, `[SIBLINGS]`, `[OUTPUT]`). The judge
must answer in a structured schema and quote the span it objects to, so an answer that cannot
point at text is not a verdict. The gate is two-sided on purpose (rows 1 and 2 above): a judge
that dismisses nothing passes a one-sided attack suite and is useless. It is **unmeasured** on
this machine.

### SSRF and rebinding via remote scan

Rows 4 to 9. The layered answer is: flag off by default, sandbox-only, scheme and port fixed,
every DNS answer checked, the validated address pinned under the TLS layer so SNI and certificate
validation still see the name, no redirects, capped body, capped time. The blocked ranges are
listed in the L5 hand-off and enumerated in `tests/worker/test_ssrf.py`.

### Spend abuse

Row 12. The ledger is process-wide and per-day, read at every decision. Exhaustion degrades to
`needs_review`, never to `dismissed`: the failure mode of a security tool has to be more work for
a human, not fewer findings. `GET /models` reports the judge as off once the ledger is spent, so
the UI never promises a stage it cannot run.

**The MCP server surface has no daily ledger, by ruling** (L8 hand-off). It is a local stdio tool
spending the user's own key, so the shared API ledger, which lives behind a database session, does
not apply; the per-scan cap (`judge_max_calls_per_scan`) and the tool cap do. A long-lived shared
`--transport http` deployment would be a different surface and must be handed the API's own
`spend.allowed` before it is exposed.

### Feedback spam

Row 14. A honeypot field plus per-IP quotas, and the only identifier stored is a salted hash.
Feedback on an unknown finding is accepted and discarded rather than probed for existence.

### Secret leakage in submitted configs

Row 10. Redaction happens at every writer and every outbound call, not once in the pipeline: a
credential that reaches the table has already leaked whatever the pipeline does afterwards. The
log filter (row 11) is the second half of the same rule.

### Model download tampering

`SIEVE_MODEL_REVISION` pins the Hub revision passed to `snapshot_download`, and
`sieve/model/infer.py` refuses an artefact whose thresholds are absent or null. The pin is
**unset by default** and no content digest is verified; see the residual risks below.

### Dependency risk

`uv.lock` and `package-lock.json` are committed; scanner versions are pinned in
`bench/versions.lock`; three compiled wheels are capped one release below the versions Windows
Smart App Control blocks (`lessons.md`).

Both audits **block** as of Task 11.1 (PRD §22). In `.github/workflows/ci.yml`, `pip-audit` runs
without `|| true` and `npm audit --audit-level=high --omit=dev` runs in the `web` job. Two notes
on what those gates actually mean:

- `pip-audit` reports PYSEC and GHSA ids and carries no severity of its own, so there is no
  `--audit-level` to set. The gate is therefore *any* advisory against the environment the job's
  `uv sync` builds, which is stricter than "high and critical only". An accepted advisory would
  have to be named in an explicit `--ignore-vuln` list beside the step and repeated below; there
  are none today, and the CI dependency set audits clean.
- `npm audit` is scoped to `--omit=dev`, so it gates the code that is actually served. The
  devDependencies are build and measurement tools — `@lhci/cli` and the Puppeteer tree under it
  — whose 7 high advisories npm can only "fix" by a major downgrade to `@lhci/cli@0.1.0`. That is
  recorded as residual risk 8 rather than papered over with a downgrade nobody wants.

### Install-time code execution in corpus capture

Row 21. Capture never runs an MCP server on the host: it builds a disposable image whose install
step disables lifecycle scripts, runs it with no host mount and no credential, speaks JSON-RPC to
it over stdio, and records an exclusion (`install_requires_scripts`) rather than retrying with
scripts enabled when the package will not install without them. Nothing captured this way has
been run on this machine: no Docker daemon was available, so the real capture test is marked
`docker` and skips (L1 hand-off).

## Residual risks

Open, known, and deliberately not fixed. Each is a decision a reviewer may disagree with, which
is why each names the code rather than a feeling. Task 11.1's security review owns this section.

### Closed by the Task 11.1 review

Kept rather than deleted: "we looked at this and fixed it" is a different claim from "nobody
ever raised it", and a reader coming from an older copy of this document deserves to find out
which one applies. Each line names the test that holds it now.

- **`ip_hash` was a bare salted SHA-256** (`sieve/api/ratelimit.py`, PRD §13.2, Phase 3 item 1).
  It is now `HMAC-SHA256(key=SIEVE_IP_HASH_SALT, message=ip)`. The ruling asked for a reason
  either way, so: a KDF was rejected. The message is an IP address, so the space is ~2^32 and a
  slow hash adds no entropy to it — an Argon2 tuned to 100 ms buys about five bits of work
  against an attacker who wants one address confirmed, and costs that 100 ms on every request
  `hit()` serves, which is a self-inflicted denial of service on the code path whose whole job is
  preventing one. What protects the pseudonym is that the key is secret and per deployment, and
  the primitive for "secret key, public message" is a MAC: `sha256(key ‖ message)` is
  length-extendable and has no proof in this role, HMAC has both. A second, smaller hole went
  with it: the salt ships as a placeholder, so a deployment that never set it was pseudonymising
  under a value anyone can read out of this repository. That is now a warning at start-up, not a
  refusal, because a checkout that will not boot is not a control anyone keeps.
  `tests/api/test_ratelimit.py::test_the_ip_hash_is_salted_and_never_the_raw_ip`,
  `tests/api/test_ratelimit.py::test_the_shipped_default_salt_is_said_out_loud_at_startup`.
- **`socket.getaddrinfo` blocked the worker's event loop** (`sieve/worker/ssrf.py`, Phase 3
  item 2). `check_url` is now a coroutine resolving through `loop.getaddrinfo` under its own
  five-second deadline, so an attacker-chosen nameserver can no longer hold the loop — and the
  heartbeat that keeps this worker's job lease alive — for as long as it pleases.
  `tests/worker/test_ssrf.py::test_a_slow_resolver_does_not_stall_the_event_loop`,
  `tests/worker/test_ssrf.py::test_a_resolver_that_never_answers_is_refused_on_the_deadline`.
  What is *not* closed is below, as risk 1.
- **`server_instructions` escaped redaction on two paths** (Phase 3 item 3). The known one was
  `sieve/adapters/_scanner_common.py`; the review found a second, in `sieve/judge/client.py`,
  which redacted `record.tool` and copied the instructions beside it straight into the block it
  sends to the Anthropic API. The pipeline redacts upstream of the judge, so the second needed a
  caller reaching the judge directly — `bench`, the MCP surface, the injection suite — but SC-5
  reads "before any API call", not "before most of them".
  `tests/judge/test_client.py::test_server_instructions_are_redacted_before_the_block_is_rendered`,
  `tests/adapters/test_scanner_common.py::test_redacted_records_redacts_server_instructions_too`.
- **`Idempotency-Key` was a second, unauthenticated name for a scan.** New in this review. There
  are no accounts, so an unguessable id is the only thing between a scan and anyone who asks;
  the header was globally unique and client-chosen, so a guessed value returned whoever had
  claimed it first. It is now namespaced by the caller's own address hash and length-bounded.
  `tests/api/test_scans.py::test_an_idempotency_key_is_scoped_to_the_client_that_chose_it`.
- **SSE streams were unbounded.** New in this review. Reads are unlimited by design (row 13), but
  a live stream is not a read: each holds a dedicated `LISTEN` connection outside the pool and
  outside every per-IP quota, so one known scan id exhausted Postgres from one host and took the
  rate-limited routes down with it. Capped process-wide in `sieve/api/sse.py`, refused with 503.
  `tests/api/test_sse.py::test_concurrent_streams_are_capped_and_the_slots_come_back`.
- **A failure message was an unredacted, unbounded writer.** New in this review. `scans.error` is
  stored, returned by `GET /scans/{id}` *and* streamed on `scan.failed`, and it was built from
  `f"{type(exc).__name__}: {exc}"` — a string the libraries on that path fill with the input they
  rejected, which is attacker-controlled tool text (SC-7). `sieve/worker/errors.py` now redacts
  and bounds it, as it does `jobs.last_error`.
  `tests/integration/test_secret_never_logged.py::test_a_secret_inside_a_failure_message_is_redacted_before_it_is_stored`,
  `tests/worker/test_main.py::test_a_credential_in_an_exception_never_reaches_jobs_last_error`.
- **`pip-audit` did not block** (Phase 3 item 6). It does now, as does `npm audit`; see
  "Dependency risk" above for what each gate covers.

The end-to-end claim those redaction fixes add up to — paste an AWS key into a tool, press scan,
and it appears in no row, no `scan_events` payload, no SSE frame, no SARIF document, no outbound
request body and no log record *at any level, DEBUG included* — is asserted against a live
database and the real worker by
`tests/integration/test_secret_never_logged.py::test_a_secret_in_a_tool_description_reaches_no_row_no_event_and_no_log`.

### Open

1. **A hostile resolver can still occupy an executor thread.** Nothing in CPython interrupts a
   blocking `getaddrinfo`, so when `sieve/worker/ssrf.py`'s deadline fires the coroutine is
   cancelled and the thread is not. The loop is free, which was the finding; the default executor
   is not unbounded-safe, and a sustained campaign of never-answering names would still starve it.
   Accepted: remote scanning is off by default, sandbox-only, behind two gates, and the sandbox
   runs one job at a time.
2. **Judge spend is charged from a positional read of `last_usage`.**
   `sieve/api/spend.py::judge_call_cost` reads a mutable attribute the judge set during the call
   that just returned, rather than a cost the call itself returns. One shared judge object used
   concurrently could charge one call's usage to another; today the pipeline is one call at a
   time per scan. A judge that reports no usage falls back to the PRD §12.4 estimate. Accepted by
   ruling in Task 11.1 and re-verified there: changing it means changing the `Judge` protocol in
   `sieve/protocols.py`, which is a contracts PR, not a security fix.
3. **`sieve/redact.py` walks values, not keys.** A credential used as a JSON-schema *property
   name* — `{"properties": {"AKIA…": {...}}}` — is rendered by `sieve/serialise.py` into a
   `[PARAM]` line and survives into the database, the judge's block and SARIF. Accepted as low:
   a key is a field name, so this is a place credentials are pasted by accident rather than by
   habit, and redacting a key would change the schema's shape for every consumer of it. That file
   is not Task 11.1's to edit (PRD §19); it is handed on as a contracts change.
4. **A worker failure still tells the client *what* failed.** The message is redacted and bounded
   now, but it still names the exception type and, for a blocked remote scan, the address a host
   resolved to. Accepted: the only party who can read a scan's error is the party who created
   that scan, and an operator with no error text cannot diagnose a failed scan at all.
5. **Reads have no quota.** `GET /findings` and `GET /sarif` will assemble up to
   `max_tools_per_scan` findings per request, unlimited times, for anyone holding a scan id.
   Accepted as the document has accepted it since Phase 3 (row 13); the SSE cap above closes the
   expensive half of it, which was the half that could take the database with it.
6. **The judge self-defence gate has never run against a real model.** Row 2 and the injection
   section say so. Until it is recorded, Sieve's injection resistance is a design claim, and this
   review changes nothing about that: it is **unmeasured**.
7. **No artefact digest.** The Hub revision pin is optional and unset; nothing verifies the bytes
   of a downloaded model against a recorded SHA.
8. **Seven high npm advisories live in `web/`'s devDependencies.** All seven resolve to
   `@lhci/cli` and the Puppeteer tree beneath it (`tmp` GHSA-52f5-9888-hmc6 and
   GHSA-ph9p-34f9-6g65, `extract-zip` GHSA-jmr9-qjv8-65gv and GHSA-7pqw-9j4j-h8q3), and npm's
   only remediation is a major downgrade to `@lhci/cli@0.1.0`. Accepted: Lighthouse CI is a
   measurement tool that runs in CI and ships nothing to a browser, and the CI gate is scoped to
   `--omit=dev` for exactly that reason. `npm audit --audit-level=high --omit=dev` reports zero.
9. **Nine `transformers` advisories are open in the `model` extra.** PYSEC-2025-217,
   PYSEC-2026-2288, PYSEC-2026-2289, PYSEC-2026-2290 and PYSEC-2026-3929 against
   `transformers==4.57.6`; every fix version is a `5.x` major. Accepted for now, and *not*
   suppressed with `--ignore-vuln`: the `model` extra is installed by neither the API image, nor
   the worker, nor the CI job that runs the blocking audit, so the gate does not need an
   exception to stay green. Upgrading is the model lane's call, since a major `transformers` bump
   moves the training path the published numbers come from.
10. **The `real_poisoned` slice and the holdout are tiny.** Every interval published from them is
    a cluster bootstrap over a small number of servers and is wide by construction. A number with
    a wide interval is not a claim.
11. **The Action installs from PyPI, where Sieve is not published.**
    `.github/workflows/self-scan.yml` fails at that step today (L7 hand-off). Nothing depends on
    it for security, but a green self-scan badge is not available as evidence.

<!-- human: rewrite -->
**Which of these are accepted and which are scheduled** is a judgement call the human owns. The
agent's reading after Task 11.1 is that 3 is the only open item that still leaks data and should
go into the next contracts PR; 9 blocks a release of the model lane rather than of this one; 6
and 7 block publishing a claim about injection resistance and about artefact integrity
respectively; and 1, 2, 4, 5 and 8 are bounded by deployment facts that a different deployment
would invalidate.

## Out of scope

- Sieve does not defend a host against a poisoned tool it has confirmed. It reports; the human or
  the CI gate acts.
- Sieve writes no detection rules and no YARA signatures (PRD §4.2), so "the scanner missed it" is
  a limitation of the scanner, not a bug in this layer.
- Physical, supply-chain-at-the-registrar and account-takeover threats against the deployment's own
  cloud provider are not modelled.
- **There are no user accounts**, so authentication, authorisation, session fixation and privilege
  escalation between users are out of scope by construction rather than by assessment. A
  deployment that adds accounts inherits none of this document's reasoning about abuse controls.
- The web front end's own threats (XSS in rendered evidence, clickjacking) are covered only by the
  headers in row 15 and by the fact that evidence is rendered as text, never as HTML; a dedicated
  front-end review is Phase 4's.
