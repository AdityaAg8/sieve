"""Is the GPU busy? The check every GPU step runs first (training plan C2, C3, §13).

Two signals, because one is not enough on the owner's machine:

* a process with its **own** reported memory is a compute process (Linux, TCC);
* on Windows (WDDM) `nvidia-smi --query-compute-apps` lists every desktop app holding a GPU
  context, all with `[N/A]` memory, and a job inside Docker does not appear per process at all.
  There the board total (`memory.used`) is the signal: above `idle_mib`, something else is
  holding the card.

Never kills, pauses or touches anything it finds; it only refuses.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Any

#: Board memory above which the card counts as in use by someone else. The owner's idle desktop
#: renders on the integrated GPU, so the discrete card sits well below this when nothing runs.
IDLE_MIB = 1024

Runner = Callable[..., Any]


@dataclass(frozen=True)
class App:
    pid: int
    name: str
    used_mib: int | None  # None where nvidia-smi reports [N/A]


@dataclass(frozen=True)
class GpuState:
    memory_used_mib: int
    memory_total_mib: int
    utilization: int
    apps: list[App]


def _int(field: str) -> int | None:
    field = field.strip()
    return int(field) if field.isdigit() else None


def _smi(run: Runner, query: str) -> list[str]:
    out = run(
        ["nvidia-smi", query, "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    if out.returncode != 0:
        raise OSError(f"nvidia-smi {query} exited {out.returncode}: {out.stderr.strip()}")
    return [line for line in out.stdout.splitlines() if line.strip()]


def query(*, run: Runner = subprocess.run) -> GpuState | None:
    """The card's state, or None when there is no `nvidia-smi` (no NVIDIA GPU to be busy)."""
    try:
        apps_out = _smi(run, "--query-compute-apps=pid,process_name,used_memory")
        board = _smi(run, "--query-gpu=memory.used,memory.total,utilization.gpu")
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    apps = []
    for line in apps_out:
        pid, *name, used = line.split(",")
        apps.append(App(pid=_int(pid) or -1, name=",".join(name).strip(), used_mib=_int(used)))
    used_total = total = util = 0
    for line in board:
        u, t, p = (line.split(",") + ["", "", ""])[:3]
        used_total += _int(u) or 0
        total += _int(t) or 0
        util = max(util, _int(p) or 0)
    return GpuState(memory_used_mib=used_total, memory_total_mib=total, utilization=util, apps=apps)


def busy_reason(
    state: GpuState, *, idle_mib: int = IDLE_MIB, own_pids: Collection[int] = ()
) -> str | None:
    """Why the card is busy, or None when it is free for this run."""
    compute = [a for a in state.apps if a.used_mib and a.pid not in own_pids]
    if compute:
        listed = "; ".join(f"pid {a.pid} {a.name} ({a.used_mib} MiB)" for a in compute)
        return f"GPU busy: another compute process holds memory: {listed}"
    if state.memory_used_mib > idle_mib:
        return (
            f"GPU busy: {state.memory_used_mib} MiB of {state.memory_total_mib} MiB in use "
            f"(> {idle_mib} MiB idle threshold, {state.utilization}% utilisation); on Windows a "
            "Docker or WDDM job shows only here"
        )
    return None


def cuda_hidden(env: Mapping[str, str]) -> bool:
    """True when `CUDA_VISIBLE_DEVICES` hides every device, so the run cannot use the GPU."""
    value = env.get("CUDA_VISIBLE_DEVICES")
    return value is not None and value.strip() in {"", "-1"}


__all__ = ["IDLE_MIB", "App", "GpuState", "busy_reason", "cuda_hidden", "query"]
