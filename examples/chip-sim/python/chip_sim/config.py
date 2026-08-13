from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from chip_sim.errors import ConfigError
from chip_sim.types import WorkloadType


@dataclass(frozen=True)
class TemplateSpec:
    workload: WorkloadType
    alias: str
    cpu_count: int
    memory_mib: int


@dataclass(frozen=True)
class WorkloadPolicy:
    default_template: str
    allowed_templates: tuple[str, ...]
    ro_drive_id: str
    ro_mount: str
    work_mount: str
    work_out: str
    ckpt_dir: str = "/mnt/work/ckpt"
    serial_log: str = "/mnt/work/out/console.log"
    gdbstub_bind: str = "127.0.0.1:1234"
    qemu_netdev: str = "user"


@dataclass
class ChipSimConfig:
    api_url: str
    api_key: str
    default_timeout_secs: int
    max_timeout_secs: int
    max_upload_file_bytes: int
    max_upload_total_bytes: int
    workloads: dict[WorkloadType, WorkloadPolicy]
    templates: dict[str, TemplateSpec]
    path: Path = field(repr=False, default=Path("."))

    def workload(self, kind: WorkloadType) -> WorkloadPolicy:
        return self.workloads[kind]

    def resolve_workload(self, explicit: WorkloadType | None) -> WorkloadType:
        if explicit is not None:
            return explicit
        raw = os.environ.get("CHIP_SIM_WORKLOAD")
        if not raw:
            return WorkloadType.RTL_SIM
        try:
            return WorkloadType(raw)
        except ValueError as exc:
            raise ConfigError(f"unknown CHIP_SIM_WORKLOAD={raw!r}") from exc

    def resolve_template(self, kind: WorkloadType, alias: str | None) -> str:
        policy = self.workload(kind)
        env_alias = os.environ.get("CHIP_SIM_TEMPLATE")
        chosen = alias or env_alias or policy.default_template
        tmpl = self.templates.get(chosen)
        if tmpl is None or chosen not in policy.allowed_templates or tmpl.workload != kind:
            allowed = ", ".join(policy.allowed_templates)
            raise ConfigError(
                f"template {chosen!r} is not allowed for {kind.value}; allowed: {allowed}"
            )
        return chosen

    def resolve_timeout(self, timeout_secs: int | None) -> int:
        env_raw = os.environ.get("CHIP_SIM_TIMEOUT_SECS")
        if timeout_secs is None and env_raw:
            timeout_secs = int(env_raw)
        if timeout_secs is None:
            return self.default_timeout_secs
        return timeout_secs


def load_config(path: Path | None = None) -> ChipSimConfig:
    cfg_path = path or Path(os.environ.get("CHIP_SIM_CONFIG", ""))
    if not cfg_path or not cfg_path.exists():
        raise ConfigError(f"config not found: {cfg_path}")
    data = tomllib.loads(cfg_path.read_text())
    agentenv = data.get("agentenv", {})
    policy = data.get("policy", {})
    workloads: dict[WorkloadType, WorkloadPolicy] = {}
    for key, raw in data.get("workloads", {}).items():
        kind = WorkloadType(key)
        workloads[kind] = WorkloadPolicy(
            default_template=raw["default_template"],
            allowed_templates=tuple(raw["allowed_templates"]),
            ro_drive_id=raw["ro_drive_id"],
            ro_mount=raw["ro_mount"],
            work_mount=raw["work_mount"],
            work_out=raw["work_out"],
            ckpt_dir=raw.get("ckpt_dir", "/mnt/work/ckpt"),
            serial_log=raw.get("serial_log", "/mnt/work/out/console.log"),
            gdbstub_bind=raw.get("gdbstub_bind", "127.0.0.1:1234"),
            qemu_netdev=raw.get("qemu_netdev", "user"),
        )
    templates: dict[str, TemplateSpec] = {}
    for alias, raw in data.get("templates", {}).items():
        templates[alias] = TemplateSpec(
            workload=WorkloadType(raw["workload"]),
            alias=raw.get("alias", alias),
            cpu_count=int(raw["cpu_count"]),
            memory_mib=int(raw["memory_mib"]),
        )
    return ChipSimConfig(
        api_url=os.environ.get("CHIP_SIM_API_URL", agentenv.get("api_url", "")),
        api_key=os.environ.get("CHIP_SIM_API_KEY", agentenv.get("api_key", "")),
        default_timeout_secs=int(policy.get("default_timeout_secs", 3600)),
        max_timeout_secs=int(policy.get("max_timeout_secs", 86400)),
        max_upload_file_bytes=int(policy.get("max_upload_file_bytes", 8 * 1024 * 1024)),
        max_upload_total_bytes=int(policy.get("max_upload_total_bytes", 32 * 1024 * 1024)),
        workloads=workloads,
        templates=templates,
        path=cfg_path,
    )
