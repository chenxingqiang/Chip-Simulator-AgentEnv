from __future__ import annotations

from pathlib import Path

from chip_sim.backend import AgentEnvBackend
from chip_sim.config import ChipSimConfig, load_config
from chip_sim.errors import ConfigError, PolicyError, SandboxError
from chip_sim.job import Job
from chip_sim.types import JobSpec, WorkloadType


class Client:
    def __init__(
        self,
        config: ChipSimConfig | None = None,
        config_path: Path | None = None,
        backend: AgentEnvBackend | None = None,
    ) -> None:
        if config is None:
            config = load_config(config_path)
        if backend is None:
            raise PolicyError("P0 Client requires an explicit backend (FakeAgentEnv or live)")
        self.config = config
        self.backend = backend
        self._jobs: dict[str, Job] = {}

    def create_job(self, spec: JobSpec) -> Job:
        kind = spec.workload
        timeout = self._validate_timeout(spec.timeout_secs)
        try:
            template = self.config.resolve_template(kind, spec.template)
        except ConfigError as exc:
            raise PolicyError(str(exc)) from exc
        self._validate_uploads(spec, kind)
        sandbox_id = self.backend.create(template, timeout, spec.env)
        self._assert_ro_drive(sandbox_id, kind)
        policy = self.config.workload(kind)
        self.backend.mkdir(sandbox_id, policy.work_out)
        if kind == WorkloadType.SOC_SW_SIM:
            self.backend.mkdir(sandbox_id, policy.ckpt_dir)
        for remote, local in spec.upload.items():
            data = Path(local).read_bytes()
            self.backend.write_file(sandbox_id, remote, data)
        job_id = sandbox_id
        job = Job(job_id, sandbox_id, spec, self.config, self.backend, policy)
        self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise SandboxError(f"job {job_id} not found")
        return job

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def _validate_timeout(self, timeout_secs: int | None) -> int:
        value = self.config.resolve_timeout(timeout_secs)
        if value <= 0 or value > self.config.max_timeout_secs:
            raise PolicyError(
                f"timeout {value} out of range (0, {self.config.max_timeout_secs}]"
            )
        return value

    def _validate_uploads(self, spec: JobSpec, kind: WorkloadType) -> None:
        policy = self.config.workload(kind)
        total = 0
        work = policy.work_mount.rstrip("/")
        for remote, local in spec.upload.items():
            path = Path(local)
            size = path.stat().st_size
            if size > self.config.max_upload_file_bytes:
                raise PolicyError(
                    "upload exceeds 8 MiB; pack into work extra-drive image"
                )
            total += size
            if remote != work and not remote.startswith(work + "/"):
                raise PolicyError(f"upload path must stay under {work}")
        if total > self.config.max_upload_total_bytes:
            raise PolicyError(
                "upload total exceeds 32 MiB; pack into work extra-drive image"
            )

    def _assert_ro_drive(self, sandbox_id: str, kind: WorkloadType) -> None:
        policy = self.config.workload(kind)
        drives = self.backend.drives(sandbox_id)
        match = [d for d in drives if d.drive_id == policy.ro_drive_id]
        if not match:
            raise SandboxError(f"missing read-only drive {policy.ro_drive_id}")
        drive = match[0]
        if not drive.read_only:
            raise SandboxError(
                f"read-only drive {policy.ro_drive_id} is writable; refusing to start"
            )
        if drive.mount_path != policy.ro_mount:
            raise SandboxError(
                f"read-only drive mount {drive.mount_path} != {policy.ro_mount}"
            )
