"""`snyk_agent_scan` adapter: illustrative-only, hard-capped at 25 invocations (SC-2, PRD §10.2).

There is no Snyk CLI or API token available in this environment. Rather than fabricate an
unverified integration against a third-party API, the adapter's transport is an injectable
`runner` callable (`list[ToolRecord] -> by_tool raw dict`, the same `{tool_name: {"findings":
[...]}}` shape used by `cisco_yara`, parsed with the same shared strict schema from
`sieve.adapters._scanner_common`); tests inject a fake.

The invocation counter is shared, mutable state that more than one process can touch at once (a
CI matrix, a human running `make baseline` while another job is mid-run), so `scan()` protects the
whole read-check-run-write sequence with an exclusive cross-process file lock (`O_CREAT|O_EXCL`
retry loop on a sibling `.lock` file — portable to Windows and Linux, unlike `fcntl`/`msvcrt`) and
writes the new count atomically (temp file + `os.replace`). A counter file that is missing when
`scan()` goes to read it — never created, or deleted between runs — is treated as 0 but logs a
WARNING naming the file, so a deliberate deletion to bypass the cap is at least on the record
(review round 1, finding 3).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sieve.adapters import _scanner_common as common
from sieve.adapters.base import SnykCapExceeded
from sieve.config import settings
from sieve.types import ScannerFinding, ScannerName, ToolRecord

SCANNER_NAME: ScannerName = "snyk_agent_scan"

RawByTool = dict[str, Any]
Runner = Callable[[list[ToolRecord]], RawByTool]

logger = logging.getLogger(__name__)


def _unconfigured_runner(records: list[ToolRecord]) -> RawByTool:
    raise RuntimeError(
        "no Snyk CLI or API token is configured in this environment; SnykAdapter.scan() "
        "requires an injected `runner` (SC-2: illustrative comparisons only, never mass-scanned)."
    )


class _ExclusiveFileLock:
    """Cross-process mutual exclusion via `O_CREAT|O_EXCL` on a sibling lock file.

    Works identically on Windows and Linux (unlike `fcntl.flock`/`msvcrt.locking`, which differ
    per platform and don't compose well with a single code path). Held across the whole
    read-check-run-write sequence in `SnykAdapter.scan`, not just the counter file access, so two
    processes racing at the cap cannot both observe count 24 and both invoke the runner.

    There is no automatic stale-lock recovery, and that is deliberate: a holder that crashes
    wedges the counter for `timeout` seconds and then raises a `TimeoutError` naming this path,
    which is loud and human-recoverable -- **delete the `.lock` file**. Reclaiming it on a
    timeout instead would reopen the double-invocation race the lock exists to close (SC-2).
    """

    def __init__(self, path: Path, timeout: float = 30.0, poll_interval: float = 0.02) -> None:
        self._path = path
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._fd: int | None = None

    def __enter__(self) -> _ExclusiveFileLock:
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                self._fd = os.open(str(self._path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"timed out waiting for the snyk invocation-counter lock at {self._path}"
                    ) from None
                time.sleep(self._poll_interval)

    def __exit__(self, *exc_info: object) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._path.unlink(missing_ok=True)


class SnykAdapter:
    """Illustrative-only comparison scanner, hard-capped at `max_invocations` (SC-2)."""

    name: str = SCANNER_NAME

    def __init__(
        self,
        counter_path: Path = Path("bench/.snyk_invocations"),
        max_invocations: int = settings.snyk_max_invocations,
        runner: Runner = _unconfigured_runner,
        version: str = "illustrative",
    ) -> None:
        self.counter_path = counter_path
        self.max_invocations = max_invocations
        self.version = version
        self._runner = runner

    def _lock_path(self) -> Path:
        return self.counter_path.with_name(self.counter_path.name + ".lock")

    def _read_count_locked(self) -> int:
        """Read the counter. Must only be called while holding `_lock_path()`."""
        if not self.counter_path.exists():
            logger.warning(
                "snyk invocation counter missing at %s; treating as 0 (a deliberate deletion "
                "still bypasses the SC-2 cap — this warning is the record of that)",
                self.counter_path,
            )
            return 0
        text = self.counter_path.read_text(encoding="utf-8").strip()
        return int(text) if text else 0

    def _write_count_locked(self, count: int) -> None:
        """Atomically replace the counter file. Must only be called while holding the lock."""
        tmp_path = self.counter_path.with_name(
            f"{self.counter_path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        )
        tmp_path.write_text(str(count), encoding="utf-8", newline="\n")
        os.replace(tmp_path, self.counter_path)

    def scan(self, records: list[ToolRecord]) -> list[ScannerFinding]:
        # SC-5: redact credential-shaped strings before anything is handed to the runner.
        redacted = common.redacted_records(records)
        with _ExclusiveFileLock(self._lock_path()):
            count = self._read_count_locked()
            if count >= self.max_invocations:
                raise SnykCapExceeded(
                    f"snyk_agent_scan hard cap reached ({count}/{self.max_invocations}); SC-2"
                )
            raw = self._runner(redacted)
            self._write_count_locked(count + 1)
        return self.normalise(raw)

    def normalise(self, raw: dict[str, Any]) -> list[ScannerFinding]:
        parsed = common.parse_by_tool(raw, schema_name=SCANNER_NAME)
        return common.to_scanner_findings(parsed, SCANNER_NAME, self.version)
