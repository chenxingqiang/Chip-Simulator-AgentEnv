from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from chip_sim.backend import AgentEnvBackend, DriveInfo, ExecResult
from chip_sim.config import ChipSimConfig
from chip_sim.errors import ReadOnlyDriveError, SandboxError
from chip_sim.metrics import Metrics
from chip_sim.types import WorkloadType


@dataclass
class FakeSandbox:
    sandbox_id: str
    template_id: str
    timeout_secs: int
    workload: WorkloadType
    drives: list[DriveInfo]
    files: dict[str, bytes] = field(default_factory=dict)
    dirs: set[str] = field(default_factory=set)
    env: dict[str, str] = field(default_factory=dict)
    exec_count: int = 0
    fork_count: int = 0
    pause_count: int = 0
    paused: bool = False
    expired: bool = False
    deleted: bool = False
    emulator_running: bool = False
    last_checkpoint: str | None = None


class FakeAgentEnv(AgentEnvBackend):
    def __init__(self, config: ChipSimConfig) -> None:
        self.config = config
        self.sandboxes: dict[str, FakeSandbox] = {}
        self.metrics = Metrics()
        self.envd_up = True
        self.force_ro_writable = False
        self.omit_ro_drive = False
        self.last_create_attached_drives = None
        self.mount_fail = False

    def sandbox(self, sandbox_id: str) -> FakeSandbox:
        rec = self.sandboxes.get(sandbox_id)
        if rec is None or rec.deleted:
            raise SandboxError(f"sandbox {sandbox_id} not found")
        return rec

    def expire_ttl(self, sandbox_id: str) -> None:
        self.sandbox(sandbox_id).expired = True

    def create(self, template_id: str, timeout_secs: int, env: dict[str, str]) -> str:
        t0 = time.perf_counter()
        tmpl = self.config.templates[template_id]
        if self.mount_fail:
            raise SandboxError("drive mount failed")
        drives = self._drives_for(tmpl.workload)
        sid = str(uuid.uuid4())
        rec = FakeSandbox(
            sandbox_id=sid,
            template_id=template_id,
            timeout_secs=timeout_secs,
            workload=tmpl.workload,
            drives=drives,
            env=dict(env),
        )
        rec.dirs.update({"/mnt/work", "/mnt/pdk", "/mnt/soc-models"})
        self.sandboxes[sid] = rec
        self.metrics.record_start(template_id, time.perf_counter() - t0)
        return sid

    def _drives_for(self, workload: WorkloadType) -> list[DriveInfo]:
        policy = self.config.workload(workload)
        drives = [
            DriveInfo(
                drive_id="work",
                mount_path=policy.work_mount,
                read_only=False,
            )
        ]
        if not self.omit_ro_drive:
            drives.append(
                DriveInfo(
                    drive_id=policy.ro_drive_id,
                    mount_path=policy.ro_mount,
                    read_only=not self.force_ro_writable,
                )
            )
        return drives

    def _require(self, sandbox_id: str) -> FakeSandbox:
        rec = self.sandbox(sandbox_id)
        if rec.expired:
            raise SandboxError("sandbox ttl expired")
        if rec.deleted:
            raise SandboxError("sandbox deleted")
        return rec

    def _is_ro_path(self, rec: FakeSandbox, path: str) -> bool:
        for drive in rec.drives:
            if drive.read_only and (
                path == drive.mount_path or path.startswith(drive.mount_path.rstrip("/") + "/")
            ):
                return True
        return False

    def mkdir(self, sandbox_id: str, path: str) -> None:
        rec = self._require(sandbox_id)
        if self._is_ro_path(rec, path):
            raise ReadOnlyDriveError(f"read-only drive: {path}")
        rec.dirs.add(path.rstrip("/"))
        parent = path.rstrip("/")
        while parent.count("/") > 1:
            parent = parent.rsplit("/", 1)[0]
            rec.dirs.add(parent)

    def write_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        rec = self._require(sandbox_id)
        if self._is_ro_path(rec, path):
            raise ReadOnlyDriveError(f"read-only drive: {path}")
        self.mkdir(sandbox_id, path.rsplit("/", 1)[0])
        rec.files[path] = data

    def read_file(self, sandbox_id: str, path: str) -> bytes:
        if not self.envd_up:
            raise SandboxError("envd unreachable")
        rec = self._require(sandbox_id)
        if path not in rec.files:
            raise SandboxError(f"file not found: {path}")
        return rec.files[path]

    def listdir(self, sandbox_id: str, path: str) -> list[str]:
        if not self.envd_up:
            raise SandboxError("envd unreachable")
        rec = self._require(sandbox_id)
        prefix = path.rstrip("/") + "/"
        names: set[str] = set()
        for p in rec.files:
            if p.startswith(prefix):
                rest = p[len(prefix) :]
                names.add(rest.split("/", 1)[0])
        for d in rec.dirs:
            if d.startswith(prefix):
                rest = d[len(prefix) :]
                if rest:
                    names.add(rest.split("/", 1)[0])
        return sorted(names)

    def exec(self, sandbox_id: str, command: str) -> ExecResult:
        rec = self._require(sandbox_id)
        rec.exec_count += 1
        if command.strip() in {"true", "/bin/true"}:
            return ExecResult(0, "", "")
        if "make -C /mnt/work sim" in command:
            rtl = rec.files.get("/mnt/work/rtl/dut.v", b"")
            text = rtl.decode("utf-8", errors="replace")
            if "ERROR_INJECT" in text:
                log = "ERROR: injected simulation failure\n"
                self.write_file(sandbox_id, "/mnt/work/out/sim.log", log.encode())
                return ExecResult(1, "", log)
            log = "PASS: simulation completed\n"
            self.write_file(sandbox_id, "/mnt/work/out/sim.log", log.encode())
            return ExecResult(0, log, "")
        if command.startswith("qemu-system") or command.startswith("/usr/bin/qemu"):
            rec.emulator_running = True
            rec.paused = False
            self.write_file(
                sandbox_id,
                "/mnt/work/out/console.log",
                b"boot: linux on rv64-virt\n",
            )
            return ExecResult(0, "qemu started\n", "")
        return ExecResult(0, command + "\n", "")

    def start_emulator(self, sandbox_id: str) -> None:
        rec = self._require(sandbox_id)
        rec.emulator_running = True
        rec.paused = False
        existing = rec.files.get("/mnt/work/out/console.log", b"")
        self.write_file(
            sandbox_id,
            "/mnt/work/out/console.log",
            existing + b"boot: linux on rv64-virt\n",
        )

    def write_checkpoint(self, sandbox_id: str, name: str) -> str:
        rec = self._require(sandbox_id)
        path = f"/mnt/work/ckpt/{name}"
        self.write_file(sandbox_id, path, b"qemu-checkpoint\n")
        rec.last_checkpoint = path
        return path

    def restore_checkpoint(self, sandbox_id: str, name: str) -> None:
        rec = self._require(sandbox_id)
        path = f"/mnt/work/ckpt/{name}"
        if path not in rec.files:
            raise SandboxError(f"checkpoint not found: {path}")
        rec.emulator_running = True
        rec.paused = False
        self.write_file(
            sandbox_id,
            "/mnt/work/out/console.log",
            b"checkpoint-restore: nested guest resumed without full boot\n",
        )

    def pause(self, sandbox_id: str) -> None:
        rec = self._require(sandbox_id)
        rec.paused = True
        rec.pause_count += 1
        rec.emulator_running = False

    def delete(self, sandbox_id: str) -> None:
        rec = self.sandbox(sandbox_id)
        rec.deleted = True
        rec.emulator_running = False

    def drives(self, sandbox_id: str) -> list[DriveInfo]:
        return list(self._require(sandbox_id).drives)
