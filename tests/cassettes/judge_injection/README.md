# Judge self-defence cassettes

Judge-level JSONL recordings for the two-sided injection gate:

- `dev.jsonl`: `tests/test_judge_injection.py` (the release gate)
- `holdout.jsonl`: `tests/test_judge_injection_holdout.py` (sealed, run once per version)

Each line is one call: `{request_hash, model, usage, output}`. No tool text, no headers, no
credentials, so there is nothing here to filter an API key out of and the file is safe to
commit. `sieve/judge/record.py` writes and replays them; `make bench` uses the same mechanism.

## Why not vcrpy / respx

Both patch `httpx`. The installed `anthropic` (1.5.0) is built on `httpx2`, so neither
intercepts a single request — verified: a VCR cassette recorded zero interactions while the
call went to the live network. Recording at the judge boundary works and is the layer
`bench/results/judge_outputs.jsonl` also uses. HTTP behaviour is unit-tested
against `httpx2.MockTransport` in `tests/judge/test_client.py`.

## Recording (once, with a key)

```
SIEVE_RERECORD=1 ANTHROPIC_API_KEY=... uv run pytest tests/test_judge_injection.py
SIEVE_RERECORD=1 ANTHROPIC_API_KEY=... uv run pytest tests/test_judge_injection_holdout.py -m holdout
```

Recording truncates the target cassette first, so a re-record replaces rather than grows it.
Until a cassette exists, its suite SKIPS with that command as the reason; it never passes
silently.
