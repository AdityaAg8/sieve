"""Network egress control for `sieve triage` (PRD §15.1).

Two concerns share this module because both are about *what leaves the process*: the socket
guard that makes `--offline` an enforceable promise rather than a documentation claim, and the
`--api-url` client, whose one job is to send redacted tool text and nothing else (SC-5).

`install_guard()` is the enforcement mechanism for **all** of `--offline`'s "no further network"
claim, not just the Hub download: once installed, a judge call or an `--api-url` request that
slips through app.py's own `--offline` branching hits this guard and fails loudly with
`NetworkBlocked`, instead of quietly leaking a request.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sieve.model.infer import FIXTURE_ID, load_classifier

if TYPE_CHECKING:
    import httpx

    from sieve.protocols import Classifier
    from sieve.types import ToolRecord, TriageResult

OFFLINE_BLOCKED_MESSAGE = "offline mode: network access refused after the model download"


class NetworkBlocked(RuntimeError):  # noqa: N818 - named by the lane brief verbatim
    """Raised by the guarded `socket.socket.connect` once `install_guard()` has run."""


class ApiError(RuntimeError):
    """The `--api-url` server refused the request or could not be reached."""


_original_connect: Any = None
_installed = False


def _blocked_connect(self: socket.socket, *_args: Any, **_kwargs: Any) -> None:
    # Close first: the caller (httpx/httpcore's connection pool, most often) has already
    # allocated the OS socket by the time `.connect()` is called, and it never gets to run its
    # own cleanup once this raises something other than the `OSError` it knows how to handle.
    # Leaving that fd open until the garbage collector gets to it is a real leak, not just a
    # noisy test.
    self.close()
    raise NetworkBlocked(OFFLINE_BLOCKED_MESSAGE)


def install_guard() -> None:
    """Monkeypatch `socket.socket.connect` to raise `NetworkBlocked`. Idempotent, process-wide."""
    global _installed, _original_connect
    if _installed:
        return
    if _original_connect is None:
        _original_connect = socket.socket.connect
    socket.socket.connect = _blocked_connect  # type: ignore[assignment]
    _installed = True


def guard_installed() -> bool:
    return _installed


def _reset_guard_for_tests() -> None:
    """Undo `install_guard()`. Test-only -- not part of the CLI's documented contract."""
    global _installed
    if _original_connect is not None:
        socket.socket.connect = _original_connect  # type: ignore[method-assign]
    _installed = False


def ensure_model_cached(model_id: str, *, revision: str | None, cache_dir: Path) -> str:
    """Resolve a local snapshot directory for `model_id`, downloading at most once.

    Cache-first: `local_files_only=True` never touches the network. Only a cache miss makes the
    one real request to the Hub that `--offline` permits (PRD §15.1).
    """
    local = Path(model_id)
    if local.is_dir():
        return str(local)

    import huggingface_hub

    try:
        return str(
            huggingface_hub.snapshot_download(
                model_id, revision=revision, cache_dir=str(cache_dir), local_files_only=True
            )
        )
    except huggingface_hub.errors.LocalEntryNotFoundError:
        return str(
            huggingface_hub.snapshot_download(model_id, revision=revision, cache_dir=str(cache_dir))
        )


def load_classifier_offline(model_id: str, *, revision: str | None, cache_dir: Path) -> Classifier:
    """The classifier for `--offline`: one permitted download, then the guard goes up.

    `install_guard()` runs here, right after the download settles and before this function
    returns control to the caller -- so nothing later in the process (a judge call, an
    `--api-url` request) can open a second socket, fixture model included.
    """
    directory = (
        model_id
        if model_id == FIXTURE_ID
        else ensure_model_cached(model_id, revision=revision, cache_dir=cache_dir)
    )
    classifier = load_classifier(directory, revision=revision, use_onnx=True, cache_dir=cache_dir)
    install_guard()
    return classifier


class TriageClient:
    """Posts one redacted `ToolRecord` per tool to `POST /api/v1/triage` (SC-5).

    Redaction happens here, client-side, in addition to the server's own: the CLI's promise is
    that redacted text is the *only* thing that leaves the process bound for `--api-url`.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        import httpx

        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TriageClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def triage_tool(self, record: ToolRecord) -> TriageResult:
        import json

        import httpx
        from pydantic import ValidationError

        from sieve.redact import redact, redact_tool
        from sieve.types import TriageResult

        tool, _kinds = redact_tool(record.tool)
        instructions = record.server_instructions
        if instructions is not None:
            instructions, _kinds2 = redact(instructions)
        redacted = record.model_copy(update={"tool": tool, "server_instructions": instructions})

        try:
            response = self._client.post("/api/v1/triage", json=redacted.model_dump(mode="json"))
        except httpx.HTTPError as exc:
            raise ApiError(f"could not reach {self._client.base_url}: {exc}") from exc

        if response.status_code != 200:
            raise ApiError(f"POST /api/v1/triage -> {response.status_code}: {response.text}")

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise ApiError(f"POST /api/v1/triage returned a 200 that is not JSON: {exc}") from exc

        try:
            return TriageResult.model_validate(payload)
        except ValidationError as exc:
            raise ApiError(
                f"POST /api/v1/triage returned a 200 that is not a TriageResult: {exc}"
            ) from exc
