Recorded VCR cassettes for the judge self-defence suite (PRD §12.5).

Empty until recorded once against the live model:

    ANTHROPIC_API_KEY=... uv run pytest tests/test_judge_injection.py --record-mode=once

The suite SKIPS, per case, while a cassette is absent and no key is set.
`x-api-key` and `authorization` are filtered out of every recording.

Known blocker: vcrpy 8.3.0 ships an httpx stub only, and anthropic 1.5.0 is built on
httpx2, so nothing is intercepted today. See docs/handoffs/L4.md.
