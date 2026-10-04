"""The eval-only `real_poisoned` slice, built from already-published artefacts.

This slice exists because v2.0 had no real poisoned example anywhere, which made both the
leakage probe and the generalisation claim unfalsifiable. It is **never trained on and never
used for any decision**: it is opened once, with the holdout, by `bench/eval_final.py`, and
reported as M13 whatever the number is.

Three constraints shape the code (PRD SC-3):

* Only *already-published* research artefacts and disclosures contribute. Nothing here scans
  a live server, and no working exploit is ever stored — tool metadata strings only.
* Server names are hashed; the citation is not. A reader can verify the source and cannot
  use the corpus as a target list.
* Where a source's licence does not permit redistribution, **no text is committed**. The
  pointer file records where to look and what the bytes must hash to, and `reconstruct()`
  rebuilds the slice at benchmark time, refusing to proceed on a hash mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from sieve.redact import redact_tool
from sieve.types import CorpusRecord, ToolDefinition

log = logging.getLogger(__name__)

DEFAULT_OUT = Path("bench/data/real_poisoned.jsonl")
DEFAULT_POINTERS = Path("bench/data/real_poisoned.pointers.json")
DEFAULT_CACHE = Path("bench/data/artefact_cache")
LABELLED_AT = "2026-09-13T00:00:00Z"
ID_PREFIX = "rp-"

_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


class ArtefactHashMismatch(Exception):  # noqa: N818 (name fixed by the lane's interface)
    """The bytes read do not hash to the value recorded as this artefact's provenance."""


class NoArtefactRows(Exception):  # noqa: N818 (reads as a condition, not an error type)
    """An artefact yielded no tool definitions. Publishing an empty slice silently is worse."""


class ArtefactSource(BaseModel):
    """A published artefact: its citation, its licence, and the local copy to read."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str
    licence: str
    redistributable: bool
    sha256: str
    path: Path | None = None


class Pointer(BaseModel):
    """What is committed for a source whose licence forbids redistribution.

    Training plan G1: a source can name its own keys (`fields`: `name_key`, `description_key`,
    `server_key`) or a `walker` for a nested shape (`"mcptox"`: `pure_tool.json`), the
    `labeller` its rows carry (`"source:mcptox"`), the pinned `commit` recorded as
    `source_commit`, and the `local_dir` holding the pinned clone instead of the cache.
    """

    url: str
    sha256: str
    licence: str
    extract: dict[str, str]  # {"path": <file in the cache>, "json_pointer": <RFC 6901>}
    walker: str | None = None
    labeller: str | None = None
    commit: str | None = None
    fields: dict[str, str] | None = None
    local_dir: str | None = None


def hash_server_name(name: str) -> str:
    """Hash the server name; the citation stays in the clear, the target does not."""
    return "hashed:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]


MCPTOX_RATIONALE = "MCPTox paradigm: {template}; security risk: {risk}"
_MCPTOX_RATIONALE = re.compile(r"^MCPTox paradigm: (.*?); security risk: (.*)$")


def mcptox_template(record: CorpusRecord) -> str | None:
    """The MCPTox attack template of a row (template x server clusters, training plan D4)."""
    match = _MCPTOX_RATIONALE.match(record.label_rationale or "")
    return match.group(1) if match else None


def mcptox_risk(record: CorpusRecord) -> str | None:
    """The MCPTox security-risk class of a row (one of 11)."""
    match = _MCPTOX_RATIONALE.match(record.label_rationale or "")
    return match.group(2) if match else None


def _mcptox_entries(payload: Any) -> list[dict[str, Any]]:
    """MCPTox `pure_tool.json`: a list of {key: tool} objects, 485 poisoned tools on 45
    servers. Template and risk class ride along into the label rationale."""
    out = []
    for group in payload:
        for tool in group.values():
            out.append(
                {
                    "name": tool["tool_name"],
                    "description": str(tool["tool_content"]).strip(),
                    "server": tool["server_name"],
                    "rationale": MCPTOX_RATIONALE.format(
                        template=tool["paradigm"], risk=tool["security risk"]
                    ),
                }
            )
    return out


def _mapped(entries: list[dict[str, Any]], fields: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "name": e[fields.get("name_key", "name")],
            "description": e.get(fields.get("description_key", "description")),
            "server": e.get(fields.get("server_key", "server"), "unknown"),
            "inputSchema": e.get("inputSchema"),
        }
        for e in entries
    ]


def _tool_entries(text: str, *, suffix: str) -> list[dict[str, Any]]:
    """Tool-definition dicts from a JSON artefact or from a write-up's ```json fences."""
    if suffix == ".json":
        payload = json.loads(text)
        entries = payload.get("tools", payload) if isinstance(payload, dict) else payload
        return [e for e in entries if isinstance(e, dict) and "name" in e]
    out: list[dict[str, Any]] = []
    for block in _JSON_FENCE.findall(text):
        try:
            entry = json.loads(block)
        except json.JSONDecodeError:
            log.info("skipping a fenced block that is not valid JSON")
            continue
        if isinstance(entry, dict) and "name" in entry:
            out.append(entry)
    return out


def _resolve(payload: Any, json_pointer: str) -> Any:
    """Minimal RFC 6901 resolution; an empty pointer is the whole document."""
    current = payload
    for raw in (p for p in json_pointer.split("/") if p):
        token = raw.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def _records_from(
    text: str,
    *,
    suffix: str,
    url: str,
    licence: str,
    sha256: str,
    start: int,
    entries: list[dict[str, Any]] | None = None,
    labeller: str = "aditya",
    commit: str | None = None,
) -> list[CorpusRecord]:
    out: list[CorpusRecord] = []
    rows = entries if entries is not None else _tool_entries(text, suffix=suffix)
    for offset, entry in enumerate(rows):
        # PRD SC-5: a published artefact can carry a credential-shaped string, and this is
        # the last point before it is written to disk. Redaction is idempotent.
        tool, redacted = redact_tool(
            ToolDefinition(
                name=entry["name"],
                title=entry.get("title"),
                description=entry.get("description"),
                inputSchema=entry.get("inputSchema") or {"type": "object"},
                outputSchema=entry.get("outputSchema"),
                annotations=entry.get("annotations"),
            )
        )
        if redacted:
            log.info("redacted %s from %s", ", ".join(redacted), entry["name"])
        out.append(
            CorpusRecord(
                id=f"{ID_PREFIX}{start + offset:04d}",
                source="real_poisoned",
                source_repo=url,
                source_commit=commit or sha256[:12],
                source_license=licence,
                capture_method="published_artifact",
                schema_fidelity="exact",
                server_name=hash_server_name(str(entry.get("server", "unknown"))),
                tool=tool,
                label="poisoned",
                label_rationale=entry.get("rationale"),
                labeller=labeller,
                labelled_at=LABELLED_AT,  # type: ignore[arg-type]
                split="eval_only",
                fold=None,
            )
        )
    return out


def _verify(path: Path, expected: str) -> bytes:
    """Read the bytes and prove they are the artefact the citation names."""
    body = path.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if digest != expected:
        raise ArtefactHashMismatch(
            f"{path}: expected sha256 {expected}, found {digest}. `source_commit` is this "
            "hash, so stamping it without checking would make the provenance a claim."
        )
    return body


def _read(source: ArtefactSource, cache_dir: Path) -> tuple[str, str]:
    """The artefact's verified text and its suffix, from the source's own path or the cache."""
    path = source.path or (cache_dir / Path(source.url).name)
    _verify(path, source.sha256)
    return path.read_text(encoding="utf-8"), path.suffix


def collect(
    sources: list[ArtefactSource],
    *,
    out: Path = DEFAULT_OUT,
    pointers: Path = DEFAULT_POINTERS,
    cache_dir: Path = DEFAULT_CACHE,
) -> list[CorpusRecord]:
    """Build the slice. Redistributable sources yield records; the rest yield pointers only."""
    records: list[CorpusRecord] = []
    pointer_entries: list[Pointer] = []
    for source in sources:
        text, suffix = _read(source, cache_dir)
        if not source.redistributable:
            path = source.path or (cache_dir / Path(source.url).name)
            pointer_entries.append(
                Pointer(
                    url=source.url,
                    sha256=source.sha256,
                    licence=source.licence,
                    extract={"path": path.name, "json_pointer": ""},
                )
            )
            continue
        records.extend(
            _records_from(
                text,
                suffix=suffix,
                url=source.url,
                licence=source.licence,
                sha256=source.sha256,
                start=len(records),
            )
        )

    if not records and not pointer_entries:
        raise NoArtefactRows(
            f"{len(sources)} source(s) yielded no tool definitions; refusing to write an "
            f"empty {out}. Check the artefact shape (a JSON object needs a `tools` key)."
        )
    log.info("writing %d eval-only records and %d pointer(s)", len(records), len(pointer_entries))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
    )
    if pointer_entries:
        pointers.parent.mkdir(parents=True, exist_ok=True)
        pointers.write_text(
            json.dumps([p.model_dump(exclude_none=True) for p in pointer_entries], indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return records


def reconstruct(pointers: list[Pointer], cache_dir: Path) -> list[CorpusRecord]:
    """Rebuild the non-redistributable rows from a verified local cache."""
    records: list[CorpusRecord] = []
    for pointer in pointers:
        root = Path(pointer.local_dir) if pointer.local_dir else cache_dir
        path = root / pointer.extract["path"]
        if not path.exists():
            raise FileNotFoundError(f"{path} is not in the artefact cache; fetch {pointer.url}")
        _verify(path, pointer.sha256)
        text = path.read_text(encoding="utf-8")
        json_pointer = pointer.extract.get("json_pointer", "")
        if path.suffix == ".json" and json_pointer:
            text = json.dumps(_resolve(json.loads(text), json_pointer))
        entries = None
        if pointer.walker == "mcptox":
            entries = _mcptox_entries(json.loads(text))
        elif pointer.walker is not None:
            raise ValueError(f"unknown walker {pointer.walker!r} for {pointer.url}")
        elif pointer.fields is not None:
            payload = json.loads(text)
            listed = payload.get("tools", payload) if isinstance(payload, dict) else payload
            entries = _mapped(list(listed), pointer.fields)
        records.extend(
            _records_from(
                text,
                suffix=path.suffix,
                url=pointer.url,
                licence=pointer.licence,
                sha256=pointer.sha256,
                start=len(records),
                entries=entries,
                labeller=pointer.labeller or "aditya",
                commit=pointer.commit,
            )
        )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m bench.collect.real_poisoned",
        description="Build the eval-only real_poisoned slice from published artefacts (SC-3).",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=None,
        help="JSON list of ArtefactSource objects (url, licence, redistributable, sha256, path)",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--pointers", type=Path, default=DEFAULT_POINTERS)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--reconstruct",
        action="store_true",
        help="rebuild from --pointers and the local cache instead of collecting",
    )
    args = parser.parse_args(argv)

    if args.reconstruct:
        entries = [
            Pointer.model_validate(e) for e in json.loads(args.pointers.read_text(encoding="utf-8"))
        ]
        records = reconstruct(entries, args.cache_dir)
        # The slice `bench/eval_final.py` opens; gitignored, never committed (SC-3).
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
        )
        print(f"reconstructed {len(records)} eval-only records to {args.out}")
        return 0

    if args.sources is None:
        parser.error("--sources is required unless --reconstruct")
    sources = [
        ArtefactSource.model_validate(e)
        for e in json.loads(args.sources.read_text(encoding="utf-8"))
    ]
    records = collect(sources, out=args.out, pointers=args.pointers, cache_dir=args.cache_dir)
    print(f"wrote {len(records)} eval-only records to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
