"""The local-LLM client shared by synthetic generation (E2) and ensemble labelling (F1).

Three properties matter more than the code:

1. **Loopback only.** The base URL must name 127.0.0.1, localhost or ::1 over plain HTTP; any
   other host is refused at construction, so no corpus text can leave the machine.
2. **Deterministic requests.** `temperature 0`, a caller-chosen `seed`, `think: false`, no
   streaming, and a JSON schema in `format` so the reply is parseable by construction.
3. **Resumable.** `RawLog` appends one JSON line per finished (model, key); a re-run reads it
   back and skips what is done, so a crash or a stop costs only the row in flight.

Requests go through a replaceable `transport` so tests never need a model.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:11434"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
RETRIES = 3

Transport = Callable[[str, bytes, float], bytes]


class NotLoopback(ValueError):  # noqa: N818 - a refusal, named for what it refuses
    """The client was pointed at something other than this machine."""


class OllamaError(RuntimeError):
    """No usable reply after every retry."""


def _post(url: str, body: bytes, timeout: float) -> bytes:
    request = urllib.request.Request(  # noqa: S310 - the URL is checked to be loopback HTTP
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return bytes(response.read())


def _check_loopback(base_url: str) -> str:
    parts = urlsplit(base_url)
    if parts.scheme != "http" or parts.hostname not in LOOPBACK_HOSTS:
        raise NotLoopback(
            f"{base_url!r} is not a loopback HTTP URL; the local model client only talks to "
            "127.0.0.1, localhost or ::1"
        )
    return base_url.rstrip("/")


@dataclass(frozen=True)
class Reply:
    parsed: dict[str, Any]
    content: str
    attempts: int
    eval_count: int | None = None
    total_duration_ns: int | None = None


@dataclass
class OllamaClient:
    base_url: str = DEFAULT_URL
    timeout: float = 600.0
    retries: int = RETRIES
    transport: Transport = field(default=_post, repr=False)

    def __post_init__(self) -> None:
        self.base_url = _check_loopback(self.base_url)

    def chat(
        self,
        model: str,
        messages: Sequence[Mapping[str, str]],
        *,
        schema: Mapping[str, Any],
        seed: int,
        options: Mapping[str, Any] | None = None,
    ) -> Reply:
        """One deterministic, schema-bound chat completion; retried on transport or parse error."""
        body = json.dumps(
            {
                "model": model,
                "messages": list(messages),
                "stream": False,
                "think": False,
                "format": dict(schema),
                "options": {**(options or {}), "temperature": 0, "seed": int(seed)},
            }
        ).encode("utf-8")
        required = list(schema.get("required", []))
        errors: list[str] = []
        for attempt in range(1, self.retries + 1):
            try:
                raw = json.loads(self.transport(f"{self.base_url}/api/chat", body, self.timeout))
                content = str(raw["message"]["content"])
                parsed = json.loads(content)
                if not isinstance(parsed, dict) or any(k not in parsed for k in required):
                    raise ValueError(f"reply lacks {required}: {content[:120]!r}")
            except (OSError, ValueError, KeyError, TypeError) as error:
                errors.append(f"{type(error).__name__}: {error}")
                continue
            return Reply(
                parsed=parsed,
                content=content,
                attempts=attempt,
                eval_count=raw.get("eval_count"),
                total_duration_ns=raw.get("total_duration"),
            )
        raise OllamaError(f"{model}: no usable reply in {self.retries} attempts: {errors}")


class RawLog:
    """Append-only JSONL of finished results, keyed by (model, key). Last write wins on read."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._done: dict[tuple[str, str], dict[str, Any]] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    self._done[(entry["model"], entry["key"])] = entry["result"]

    def get(self, model: str, key: str) -> dict[str, Any] | None:
        return self._done.get((model, key))

    def put(self, model: str, key: str, result: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"model": model, "key": key, "result": dict(result)}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        self._done[(model, key)] = dict(result)


__all__ = ["DEFAULT_URL", "NotLoopback", "OllamaClient", "OllamaError", "RawLog", "Reply"]
