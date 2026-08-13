# Chip-Sim Vertical: Interfaces and Layout

This document specifies the P0 SDK surface, the native AgentENV calls it
wraps, configuration, and the repository tree. Semantics live in
[design.md](./design.md). Tests live in [test-plan.md](./test-plan.md).

## 1. Layering

```
Agent  ──►  chip_sim.Client          (Python, P0)
              │
              ├── AgentENV HTTP      create / timeout / pause / delete / snapshots
              └── envd (E2B-shaped)  commands.run / files write-read
                    │
Human ──►  aenv sim …                thin CLI, same Client, no second impl
```

E2B `Sandbox.create(templateId)` cannot attach extra drives or pick
cold-start resources. Warm start from a **pre-baked snapshot alias** is the
P0 create path. The SDK still exposes E2B-like `commands` and `files` after
the sandbox is running.

## 2. Configuration

Operator file (not AgentENV `config/default.toml`):

```toml
# examples/chip-sim/config/chip-sim.toml
[agentenv]
api_url = "http://127.0.0.1:8000"
api_key = "dummy"

[policy]
default_timeout_secs = 3600
max_timeout_secs = 86400
default_template = "chip-sim-8c"
allowed_templates = ["chip-sim-2c", "chip-sim-8c", "chip-sim-32c"]
pdk_mount = "/mnt/pdk"
work_mount = "/mnt/work"
work_out = "/mnt/work/out"

[templates.chip-sim-2c]
alias = "chip-sim-2c"
cpu_count = 2
memory_mib = 4096

[templates.chip-sim-8c]
alias = "chip-sim-8c"
cpu_count = 8
memory_mib = 16384

[templates.chip-sim-32c]
alias = "chip-sim-32c"
cpu_count = 32
memory_mib = 65536
```

Environment overrides: `CHIP_SIM_API_URL`, `CHIP_SIM_API_KEY`,
`CHIP_SIM_TEMPLATE`, `CHIP_SIM_TIMEOUT_SECS`.

## 3. Snapshot aliases (create-time contract)

| Alias | vCPU | Memory | Drives |
|---|---|---|---|
| `chip-sim-2c` | 2 | 4 GiB | `pdk` RO `/mnt/pdk`, `work` RW `/mnt/work` |
| `chip-sim-8c` | 8 | 16 GiB | same |
| `chip-sim-32c` | 32 | 64 GiB | same |

Build (operator, once per alias) uses **cold start + snapshot**, not
`aenv build` (no `COPY`/`ADD`):

```http
POST /sandboxes-cold
{
  "image": "ghcr.io/<org>/chip-sim-base:<tag>",
  "cpuCount": 8,
  "memoryMB": 16384,
  "timeout": 3600,
  "attachedDrives": [
    {
      "driveID": "pdk",
      "readOnly": true,
      "mountPath": "/mnt/pdk",
      "source": { "image": "ghcr.io/<org>/chip-sim-pdk-sky130:<tag>" }
    },
    {
      "driveID": "work",
      "readOnly": false,
      "mountPath": "/mnt/work",
      "diskSizeMB": 32768,
      "source": { "image": "ghcr.io/<org>/chip-sim-work-empty:32g" }
    }
  ]
}
```

Then exec a smoke command and:

```http
POST /sandboxes/{id}/snapshots
{ "name": "chip-sim-8c" }
```

Job create is warm start only:

```http
POST /sandboxes
{
  "templateID": "chip-sim-8c",
  "timeout": 3600,
  "autoPause": true,
  "envVars": { "CHIP_SIM_JOB_ID": "..." }
}
```

The SDK rejects unknown template aliases and timeouts `> max_timeout_secs`.

## 4. Python SDK

Package: `chip_sim` under `examples/chip-sim/python/chip_sim/`.

### 4.1 Types

```python
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping

class JobState(str, Enum):
    CREATING = "creating"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LOST = "lost"          # sandbox gone, artifacts maybe missing
    CLOSED = "closed"

@dataclass(frozen=True)
class JobSpec:
    command: str                     # run under /mnt/work
    template: str | None = None      # default from config
    timeout_secs: int | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    # Small files only. Large trees belong in a work extra-drive image.
    upload: Mapping[str, Path] = field(default_factory=dict)
    workdir: str = "/mnt/work"
    collect_globs: tuple[str, ...] = ("/mnt/work/out/**",)

@dataclass
class JobResult:
    job_id: str
    sandbox_id: str
    state: JobState
    exit_code: int | None
    stdout: str
    stderr: str
    artifact_dir: Path | None
    artifacts_lost: bool
```

`upload` keys are remote paths. The SDK must refuse a single file larger
than 8 MiB and a total upload larger than 32 MiB (P0 constants; operator
may raise later). Over-size inputs must be packed into the work drive
image instead.

### 4.2 Client

```python
class Client:
    def __init__(self, config_path: str | None = None): ...

    def create_job(self, spec: JobSpec) -> Job: ...
    def get_job(self, job_id: str) -> Job: ...
    def list_jobs(self) -> list[Job]: ...

class Job:
    id: str
    sandbox_id: str
    spec: JobSpec

    def wait(self, timeout_secs: int | None = None) -> JobResult: ...
    def collect(self, dest: Path) -> Path: ...
    def close(self) -> None: ...
    # E2B-shaped escape hatches for agents:
    def run(self, command: str, timeout_secs: int | None = None) -> JobResult: ...
    def write_text(self, remote_path: str, content: str) -> None: ...
    def read_text(self, remote_path: str) -> str: ...
```

`create_job` sequence:

1. Validate template ∈ `allowed_templates`, timeout ∈ `(0, max]`.
2. `POST /sandboxes` with `templateID`.
3. Assert snapshot-inherited PDK drive is read-only (fail closed).
4. `mkdir -p /mnt/work/out`.
5. Upload `spec.upload` via envd.
6. Return `Job` (command not started until `wait()` / `run()`).

`wait()`:

1. `cd $workdir && <command>`, stream stdout/stderr.
2. On completion, `collect()` into a local temp dir.
3. `close()`: `POST /timeout` is not enough — delete or leave running
   based on `keep_sandbox` (default delete). Always attempt `collect()`
   first.

`close()` on a dead sandbox sets `artifacts_lost=True` if envd is
unreachable.

P0 does not call `POST /sandboxes/{id}/fork`.

### 4.3 CLI wrapper

```text
aenv sim templates              # list allowed aliases
aenv sim run --template chip-sim-8c --upload ./rtl:/mnt/work/rtl -- make -C /mnt/work sim
aenv sim collect <job-id> ./out
aenv sim rm <job-id>
```

Implemented in `examples/chip-sim/python/chip_sim/cli.py`, invoking
`chip_sim.Client` only.

## 5. Guest layout

```
/mnt/pdk/                  # Sky130, read-only
/mnt/work/                 # per-job workspace
  rtl/                     # optional uploaded or image-baked sources
  tb/
  out/                     # required output directory
    sim.log
    *.vcd                  # optional
    junit.xml              # optional
```

P0 demo RTL lives in `examples/chip-sim/demos/single-job/rtl/` and is
uploaded (it is tiny). The regression demo uses one command string per
testcase, still via upload, not fork.

## 6. Images

| Image | Role | Notes |
|---|---|---|
| `chip-sim-base` | Toolchain rootfs | Ubuntu + Verilator + Yosys + Python + cocotb. No PDK, no DUT. |
| `chip-sim-pdk-sky130` | RO extra drive | Packed as overlaybd-capable OCI. |
| `chip-sim-work-empty` | RW extra drive | Empty ext4/overlaybd, default 32 GiB virtual size. |

Dockerfiles are build inputs. Publication to a registry is operator-side;
the SDK only consumes snapshot aliases.

## 7. P1 sidecar (interface only)

Not implemented in P0. Contract:

```
POST {url}/sandbox-hook/start-fresh
POST {url}/sandbox-hook/start-resume
POST {url}/sandbox-hook/patch-params
POST {url}/sandbox-hook/stop
```

Body fields are AgentENV's existing hook schema. Sidecar interprets
`customExtensionParams.chipSim` as in [design.md](./design.md) §5.

Listen on `127.0.0.1` only. Node config:

```toml
[custom_extension]
url = "http://127.0.0.1:18080"
```

## 8. Repository layout

New files only under `docs/src/vertical/chip-sim/` (this design) and, at
implementation time, `examples/chip-sim/`:

```
docs/src/vertical/chip-sim/
  design.md
  interfaces.md          # this file
  test-plan.md

examples/chip-sim/                 # P0 implementation (not in this design PR)
  README.md
  config/chip-sim.toml
  images/
    base/Dockerfile
    pdk-sky130/README.md           # packing notes, not the PDK blobs
    work-empty/README.md
  python/
    pyproject.toml
    chip_sim/
      __init__.py
      client.py
      config.py
      job.py
      artifacts.py
      agentenv.py                  # HTTP wrapper
      cli.py
    tests/
      test_config.py
      test_job_spec.py
      test_artifacts.py
      test_client_fake.py          # FakeAgentEnv
  demos/
    single-job/
      rtl/adder.v
      tb/test_adder.py
      Makefile
    regression/
      cases/*.v
      run_batch.py                 # N × Client.create_job, no fork
  scripts/
    publish_snapshots.sh           # cold-start + snapshot for 2c/8c/32c
```

AgentENV crates, `src/`, `storage/`, and `services/` stay untouched in P0
and P1 unless a later P2 patch is explicitly approved.
