"""The "is the GPU busy?" check every GPU step runs first (training plan C2/C3, §13)."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from bench import gpu

# What `nvidia-smi` prints on the owner's Windows laptop: WDDM lists every desktop app with a
# GPU context and reports no per-process memory, so only the board total shows a Docker job.
WINDOWS_APPS = """\
3420, [Insufficient Permissions], [N/A]
13840, C:\\Windows\\explorer.exe, [N/A]
30280, C:\\Program Files\\WindowsApps\\Claude_2.16120.0.0_x64__pzs8sxrjxfjjc\\app\\claude.exe, [N/A]
"""
LINUX_TRAINING = """\
4242, /usr/bin/python3, 9120
"""


def _runner(apps: str, memory: str) -> Any:
    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        query = next(a for a in argv if a.startswith("--query"))
        out = apps if "compute-apps" in query else memory
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    return run


def test_windows_desktop_apps_alone_are_not_busy() -> None:
    state = gpu.query(run=_runner(WINDOWS_APPS, "412, 12282, 3\n"))
    assert state is not None
    assert len(state.apps) == 3
    assert gpu.busy_reason(state) is None


def test_a_docker_job_shows_only_in_the_board_memory() -> None:
    state = gpu.query(run=_runner(WINDOWS_APPS, "8396, 12282, 88\n"))
    assert state is not None
    reason = gpu.busy_reason(state)
    assert reason is not None and "8396 MiB" in reason


def test_a_process_with_its_own_memory_is_a_compute_process() -> None:
    state = gpu.query(run=_runner(LINUX_TRAINING, "300, 12282, 0\n"))
    assert state is not None
    reason = gpu.busy_reason(state)
    assert reason is not None and "4242" in reason
    assert gpu.busy_reason(state, own_pids={4242}) is None


def test_the_idle_threshold_is_a_parameter() -> None:
    state = gpu.query(run=_runner(WINDOWS_APPS, "1500, 12282, 1\n"))
    assert state is not None
    assert gpu.busy_reason(state) is not None
    assert gpu.busy_reason(state, idle_mib=2048) is None


def test_no_nvidia_smi_means_no_state() -> None:
    def missing(argv: list[str], **_: Any) -> Any:
        raise FileNotFoundError("nvidia-smi")

    assert gpu.query(run=missing) is None


@pytest.mark.parametrize(
    ("env", "hidden"),
    [({"CUDA_VISIBLE_DEVICES": "-1"}, True), ({"CUDA_VISIBLE_DEVICES": ""}, True),
     ({"CUDA_VISIBLE_DEVICES": "0"}, False), ({}, False)],
)  # fmt: skip
def test_cuda_hidden(env: dict[str, str], hidden: bool) -> None:
    assert gpu.cuda_hidden(env) is hidden
