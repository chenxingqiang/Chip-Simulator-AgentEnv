from __future__ import annotations

from pathlib import Path

from chip_sim.backend import AgentEnvBackend
from chip_sim.errors import SandboxError


def collect_out(backend: AgentEnvBackend, sandbox_id: str, work_out: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    names = backend.listdir(sandbox_id, work_out)
    for name in names:
        remote = f"{work_out.rstrip('/')}/{name}"
        try:
            data = backend.read_file(sandbox_id, remote)
        except SandboxError:
            continue
        (dest / name).write_bytes(data)
    return dest
