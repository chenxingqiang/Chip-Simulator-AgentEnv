from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from chip_sim.artifacts import collect_out
from chip_sim.backend import AgentEnvBackend
from chip_sim.config import ChipSimConfig, WorkloadPolicy
from chip_sim.errors import PolicyError, SandboxError
from chip_sim.fake import FakeAgentEnv
from chip_sim.types import JobResult, JobSpec, JobState, WorkloadType


class Job:
    def __init__(
        self,
        job_id: str,
        sandbox_id: str,
        spec: JobSpec,
        config: ChipSimConfig,
        backend: AgentEnvBackend,
        policy: WorkloadPolicy,
    ) -> None:
        self.id = job_id
        self.sandbox_id = sandbox_id
        self.spec = spec
        self._config = config
        self._backend = backend
        self._policy = policy
        self._closed = False
        self._last_result: JobResult | None = None
        self._artifact_dir: Path | None = None

    def emulator_argv(self, extra: tuple[str, ...] | None = None) -> list[str]:
        extra = extra if extra is not None else self.spec.qemu_extra_args
        joined = " ".join(extra)
        if "-enable-kvm" in extra or "tap" in joined:
            raise ValueError("nested kvm/tap is forbidden; use TCG and user-net")
        argv = [
            "qemu-system-riscv64",
            "-machine",
            "virt",
            "-nographic",
            "-accel",
            "tcg",
            "-serial",
            f"file:{self._policy.serial_log}",
            "-netdev",
            "user,id=net0",
            "-device",
            "virtio-net-device,netdev=net0",
        ]
        if self.spec.enable_gdbstub:
            argv.extend(["-gdb", f"tcp:{self._policy.gdbstub_bind}"])
        argv.extend(extra)
        return argv

    def start_emulator(self) -> None:
        if isinstance(self._backend, FakeAgentEnv):
            self._backend.start_emulator(self.sandbox_id)
            return
        cmd = " ".join(self.emulator_argv())
        self._backend.exec(self.sandbox_id, cmd)

    def checkpoint(self, name: str = "default") -> str:
        if isinstance(self._backend, FakeAgentEnv):
            return self._backend.write_checkpoint(self.sandbox_id, name)
        path = f"{self._policy.ckpt_dir}/{name}"
        self._backend.exec(self.sandbox_id, f"mkdir -p {self._policy.ckpt_dir}")
        self._backend.write_file(self.sandbox_id, path, b"checkpoint")
        return path

    def restore(self, name: str = "default") -> None:
        if isinstance(self._backend, FakeAgentEnv):
            self._backend.restore_checkpoint(self.sandbox_id, name)
            return
        self.start_emulator()

    def console_log(self) -> str:
        try:
            return self._backend.read_file(self.sandbox_id, self._policy.serial_log).decode()
        except SandboxError:
            return ""

    def gdb(self, gdb_command: str):
        cmd = (
            "gdb-multiarch -batch "
            f'-ex "target remote {self._policy.gdbstub_bind}" '
            f'-ex "{gdb_command}"'
        )
        return self.run(cmd)

    def run(self, command: str, timeout_secs: int | None = None) -> JobResult:
        _ = timeout_secs
        try:
            exec_result = self._backend.exec(self.sandbox_id, command)
        except SandboxError as exc:
            result = JobResult(
                job_id=self.id,
                sandbox_id=self.sandbox_id,
                state=JobState.LOST,
                exit_code=None,
                stdout="",
                stderr=str(exc),
                artifact_dir=None,
                artifacts_lost=True,
            )
            self._last_result = result
            return result
        state = JobState.SUCCEEDED if exec_result.exit_code == 0 else JobState.FAILED
        result = JobResult(
            job_id=self.id,
            sandbox_id=self.sandbox_id,
            state=state,
            exit_code=exec_result.exit_code,
            stdout=exec_result.stdout,
            stderr=exec_result.stderr,
            artifact_dir=self._artifact_dir,
            artifacts_lost=False,
        )
        self._last_result = result
        return result

    def wait(self, timeout_secs: int | None = None) -> JobResult:
        if self.spec.workload == WorkloadType.SOC_SW_SIM and not self.spec.command:
            self.start_emulator()
            result = JobResult(
                job_id=self.id,
                sandbox_id=self.sandbox_id,
                state=JobState.SUCCEEDED,
                exit_code=0,
                stdout=self.console_log(),
                stderr="",
                artifact_dir=None,
                artifacts_lost=False,
            )
        else:
            command = self.spec.command or "true"
            result = self.run(command, timeout_secs)
        dest = Path(tempfile.mkdtemp(prefix="chip-sim-"))
        try:
            self.collect(dest)
            result.artifact_dir = dest
            result.artifacts_lost = False
        except SandboxError:
            result.artifacts_lost = True
            result.state = JobState.LOST
        self._last_result = result
        if not self.spec.keep_sandbox:
            closed = self.close()
            result.artifacts_lost = result.artifacts_lost or closed.artifacts_lost
            if closed.state == JobState.LOST:
                result.state = JobState.LOST
        return result

    def collect(self, dest: Path) -> Path:
        self._artifact_dir = collect_out(
            self._backend, self.sandbox_id, self._policy.work_out, dest
        )
        return self._artifact_dir

    def close(self) -> JobResult:
        if self._closed:
            return self._last_result or JobResult(
                job_id=self.id,
                sandbox_id=self.sandbox_id,
                state=JobState.CLOSED,
                exit_code=None,
                stdout="",
                stderr="",
                artifact_dir=self._artifact_dir,
                artifacts_lost=False,
            )
        artifacts_lost = False
        state = JobState.CLOSED
        if self._artifact_dir is None:
            dest = Path(tempfile.mkdtemp(prefix="chip-sim-"))
            try:
                self.collect(dest)
            except SandboxError:
                artifacts_lost = True
                state = JobState.LOST
        if not self.spec.keep_sandbox:
            try:
                self._backend.delete(self.sandbox_id)
            except SandboxError:
                artifacts_lost = True
        self._closed = True
        result = JobResult(
            job_id=self.id,
            sandbox_id=self.sandbox_id,
            state=state,
            exit_code=self._last_result.exit_code if self._last_result else None,
            stdout=self._last_result.stdout if self._last_result else "",
            stderr=self._last_result.stderr if self._last_result else "",
            artifact_dir=self._artifact_dir,
            artifacts_lost=artifacts_lost,
        )
        self._last_result = result
        return result

    def write_text(self, remote_path: str, content: str) -> None:
        self._backend.write_file(self.sandbox_id, remote_path, content.encode())

    def read_text(self, remote_path: str) -> str:
        return self._backend.read_file(self.sandbox_id, remote_path).decode()
