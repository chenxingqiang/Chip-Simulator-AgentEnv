from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DriveInfo:
    drive_id: str
    mount_path: str
    read_only: bool


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str


class AgentEnvBackend:
    def create(
        self,
        template_id: str,
        timeout_secs: int,
        env: dict[str, str],
    ):
        raise NotImplementedError

    def exec(self, sandbox_id: str, command: str) -> ExecResult:
        raise NotImplementedError

    def write_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        raise NotImplementedError

    def read_file(self, sandbox_id: str, path: str) -> bytes:
        raise NotImplementedError

    def mkdir(self, sandbox_id: str, path: str) -> None:
        raise NotImplementedError

    def listdir(self, sandbox_id: str, path: str) -> list[str]:
        raise NotImplementedError

    def pause(self, sandbox_id: str) -> None:
        raise NotImplementedError

    def delete(self, sandbox_id: str) -> None:
        raise NotImplementedError

    def drives(self, sandbox_id: str) -> list[DriveInfo]:
        raise NotImplementedError
