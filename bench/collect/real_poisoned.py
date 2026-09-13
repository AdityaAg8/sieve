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

from sieve.types import CorpusRecord, ToolDefinition

log = logging.getLogger(__name__)

DEFAULT_OUT = Path("bench/data/real_poisoned.jsonl")
DEFAULT_POINTERS = Path("bench/data/real_poisoned.pointers.json")
DEFAULT_CACHE = Path("bench/data/artefact_cache")
LABELLED_AT = "2026-09-13T00:00:00Z"
ID_PREFIX = "rp-"

_JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


class ArtefactHashMismatch(Exception):  # noqa: N818 (name fixed by the lane's interface)
    """The cached bytes do not hash to the value recorded in the pointer."""


class ArtefactSource(BaseModel):
    """A published artefact: its citation, its licence, and the local copy to read."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str
    licence: str
    redistributable: bool
    sha256: str
    path: Path | None = None


class Pointer(BaseModel):
    """What is committed for a source whose licence forbids redistribution."""

    url: str
    sha256: str
    licence: str
    extract: dict[str, str]  # {"path": <file in the cache>, "json_pointer": <RFC 6901>}


def hash_server_name(name: str) -> str:
    """Hash the server name; the citation stays in the clear, the target does not."""
    return "hashed:" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]


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
) -> list[CorpusRecord]:
    out: list[CorpusRecord] = []
    for offset, entry in enumerate(_tool_entries(text, suffix=suffix)):
        tool = ToolDefinition(
            name=entry["name"],
            title=entry.get("title"),
            description=entry.get("description"),
            inputSchema=entry.get("inputSchema") or {"type": "object"},
            outputSchema=entry.get("outputSchema"),
            annotations=entry.get("annotations"),
        )
        out.append(
            CorpusRecord(
                id=f"{ID_PREFIX}{start + offset:04d}",
                source="real_poisoned",
                source_repo=url,
                source_commit=sha256[:12],
                source_license=licence,
                capture_method="published_artifact",
                schema_fidelity="exact",
                server_name=hash_server_name(str(entry.get("server", "unknown"))),
                tool=tool,
                label="poisoned",
                labeller="aditya",
                labelled_at=LABELLED_AT,  # type: ignore[arg-type]
                split="eval_only",
                fold=None,
            )
        )
    return out


def _read(source: ArtefactSource, cache_dir: Path) -> tuple[str, str]:
    """The artefact's text and its suffix, from the source's own path or the cache."""
    path = source.path or (cache_dir / Path(source.url).name)
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

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "".join(r.model_dump_json() + "\n" for r in records), encoding="utf-8", newline="\n"
    )
    if pointer_entries:
        pointers.parent.mkdir(parents=True, exist_ok=True)
        pointers.write_text(
            json.dumps([p.model_dump() for p in pointer_entries], indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return records


def reconstruct(pointers: list[Pointer], cache_dir: Path) -> list[CorpusRecord]:
    """Rebuild the non-redistributable rows from a verified local cache."""
    records: list[CorpusRecord] = []
    for pointer in pointers:
        path = cache_dir / pointer.extract["path"]
        if not path.exists():
            raise FileNotFoundError(f"{path} is not in the artefact cache; fetch {pointer.url}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != pointer.sha256:
            raise ArtefactHashMismatch(
                f"{path}: expected sha256 {pointer.sha256}, found {digest}. Refusing to "
                "rebuild the eval-only slice from bytes that are not the cited artefact."
            )
        text = path.read_text(encoding="utf-8")
        json_pointer = pointer.extract.get("json_pointer", "")
        if path.suffix == ".json" and json_pointer:
            text = json.dumps(_resolve(json.loads(text), json_pointer))
        records.extend(
            _records_from(
                text,
                suffix=path.suffix,
                url=pointer.url,
                licence=pointer.licence,
                sha256=pointer.sha256,
                start=len(records),
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
        required=True,
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
        print(f"reconstructed {len(records)} eval-only records")
        return 0

    sources = [
        ArtefactSource.model_validate(e)
        for e in json.loads(args.sources.read_text(encoding="utf-8"))
    ]
    records = collect(sources, out=args.out, pointers=args.pointers, cache_dir=args.cache_dir)
    print(f"wrote {len(records)} eval-only records to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
