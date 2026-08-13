from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Mapping


class WorkloadType(str, Enum):
    RTL_SIM = "rtl-sim"
    SOC_SW_SIM = "soc-sw-sim"


class JobState(str, Enum):
    CREATING = "creating"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LOST = "lost"
    CLOSED = "closed"


class JobSpec:
    __slots__ = (
        "workload",
        "command",
        "template",
        "timeout_secs",
        "env",
        "upload",
        "workdir",
        "collect_globs",
        "soc",
        "qemu_extra_args",
        "enable_gdbstub",
        "keep_sandbox",
    )

    def __init__(
        self,
        workload: WorkloadType,
        command: str | None = None,
        template: str | None = None,
        timeout_secs: int | None = None,
        env: Mapping[str, str] | None = None,
        upload: Mapping[str, Path] | None = None,
        workdir: str | None = None,
        collect_globs: tuple[str, ...] | None = None,
        soc: str = "rv64-virt",
        qemu_extra_args: tuple[str, ...] = (),
        enable_gdbstub: bool = True,
        keep_sandbox: bool = False,
    ) -> None:
        self.workload = workload
        self.command = command
        self.template = template
        self.timeout_secs = timeout_secs
        self.env = dict(env or {})
        self.upload = dict(upload or {})
        self.workdir = workdir
        self.collect_globs = collect_globs
        self.soc = soc
        self.qemu_extra_args = qemu_extra_args
        self.enable_gdbstub = enable_gdbstub
        self.keep_sandbox = keep_sandbox


class JobResult:
    def __init__(
        self,
        job_id: str,
        sandbox_id: str,
        state: JobState,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        artifact_dir: Path | None,
        artifacts_lost: bool,
        checkpoint_path: str | None = None,
    ) -> None:
        self.job_id = job_id
        self.sandbox_id = sandbox_id
        self.state = state
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.artifact_dir = artifact_dir
        self.artifacts_lost = artifacts_lost
        self.checkpoint_path = checkpoint_path
