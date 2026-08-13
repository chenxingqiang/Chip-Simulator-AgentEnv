# Chip-Sim Vertical: Interfaces and Layout

Primary consumer is the **chip LLM agent**. These APIs exist to close
generate → simulate → feedback. Humans use the same client via a thin
CLI; they are not a second product.

P0 SDK surface, AgentENV calls, and repository tree for **both**
`rtl-sim` and `soc-sw-sim`. Semantics: [design.md](./design.md).
Tests: [test-plan.md](./test-plan.md).

## 1. Layering

```
Agent  ──►  chip_sim.Client
              ├── AgentENV HTTP   create / timeout / pause / delete / snapshots
              └── envd            commands.run / files
Human ──►  aenv sim …             same Client
```

One package. Workload is a field, not a second SDK. Warm start from a
pre-baked snapshot alias (E2B `Sandbox.create` cannot attach extra drives).

## 2. Configuration

```toml
# examples/chip-sim/config/chip-sim.toml  (shared operator file)
[agentenv]
api_url = "http://127.0.0.1:8000"
api_key = "dummy"

[policy]
default_timeout_secs = 3600
max_timeout_secs = 86400
max_upload_file_bytes = 8388608      # 8 MiB
max_upload_total_bytes = 33554432    # 32 MiB

[workloads.rtl-sim]
default_template = "chip-sim-8c"
allowed_templates = ["chip-sim-2c", "chip-sim-8c", "chip-sim-32c"]
ro_drive_id = "pdk"
ro_mount = "/mnt/pdk"
work_mount = "/mnt/work"
work_out = "/mnt/work/out"

[workloads.soc-sw-sim]
default_template = "chip-sw-sim-4c"
allowed_templates = ["chip-sw-sim-2c", "chip-sw-sim-4c", "chip-sw-sim-8c"]
ro_drive_id = "soc-models"
ro_mount = "/mnt/soc-models"
work_mount = "/mnt/work"
work_out = "/mnt/work/out"
ckpt_dir = "/mnt/work/ckpt"
serial_log = "/mnt/work/out/console.log"
gdbstub_bind = "127.0.0.1:1234"
qemu_netdev = "user"

[templates.chip-sim-2c]
workload = "rtl-sim"
alias = "chip-sim-2c"
cpu_count = 2
memory_mib = 4096

[templates.chip-sim-8c]
workload = "rtl-sim"
alias = "chip-sim-8c"
cpu_count = 8
memory_mib = 16384

[templates.chip-sim-32c]
workload = "rtl-sim"
alias = "chip-sim-32c"
cpu_count = 32
memory_mib = 65536

[templates.chip-sw-sim-2c]
workload = "soc-sw-sim"
alias = "chip-sw-sim-2c"
cpu_count = 2
memory_mib = 4096

[templates.chip-sw-sim-4c]
workload = "soc-sw-sim"
alias = "chip-sw-sim-4c"
cpu_count = 4
memory_mib = 8192

[templates.chip-sw-sim-8c]
workload = "soc-sw-sim"
alias = "chip-sw-sim-8c"
cpu_count = 8
memory_mib = 16384
```

Env: `CHIP_SIM_API_URL`, `CHIP_SIM_API_KEY`, `CHIP_SIM_TEMPLATE`,
`CHIP_SIM_TIMEOUT_SECS`, `CHIP_SIM_WORKLOAD`.

## 3. Snapshot aliases

### 3.1 RTL (`rtl-sim`)

| Alias | vCPU | Memory | Drives |
|---|---|---|---|
| `chip-sim-2c` | 2 | 4 GiB | `pdk` RO `/mnt/pdk`, `work` RW `/mnt/work` |
| `chip-sim-8c` | 8 | 16 GiB | same |
| `chip-sim-32c` | 32 | 64 GiB | same |

Cold-start recipe: toolchain image + Sky130 + empty work, smoke
`verilator --version`, snapshot.

### 3.2 SoC (`soc-sw-sim`)

| Alias | vCPU | Memory | Drives |
|---|---|---|---|
| `chip-sw-sim-2c` | 2 | 4 GiB | `soc-models` RO `/mnt/soc-models`, `work` RW `/mnt/work` |
| `chip-sw-sim-4c` | 4 | 8 GiB | same |
| `chip-sw-sim-8c` | 8 | 16 GiB | same |

```http
POST /sandboxes-cold
{
  "image": "ghcr.io/<org>/chip-sw-sim-base:<tag>",
  "cpuCount": 4,
  "memoryMB": 8192,
  "timeout": 3600,
  "network": {
    "denyOut": ["0.0.0.0/0"],
    "allowOut": ["10.0.0.20/32"]
  },
  "attachedDrives": [
    {
      "driveID": "soc-models",
      "readOnly": true,
      "mountPath": "/mnt/soc-models",
      "source": { "image": "ghcr.io/<org>/chip-sw-sim-soc-models-rv64:<tag>" }
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

Smoke: `qemu-system-riscv64 --version`. Snapshot name `chip-sw-sim-4c`.
P0 create for jobs is warm start only (`POST /sandboxes` + `templateID`).

`customExtensionParams` are omitted in P0 (no sidecar). When P1 enables
the sidecar they look like [design.md](./design.md) §6.

## 4. Python SDK

Package: `chip_sim` in `examples/chip-sim/python/chip_sim/` (shared).

### 4.1 Types

```python
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

@dataclass(frozen=True)
class JobSpec:
    workload: WorkloadType
    command: str | None = None       # rtl-sim: make sim; soc: optional wrapper
    template: str | None = None
    timeout_secs: int | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    upload: Mapping[str, Path] = field(default_factory=dict)
    workdir: str | None = None       # default per workload
    collect_globs: tuple[str, ...] | None = None
    # soc-sw-sim only (ignored for rtl-sim)
    soc: str = "rv64-virt"
    qemu_extra_args: tuple[str, ...] = ()
    enable_gdbstub: bool = True      # bind 127.0.0.1:1234 inside sandbox

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
    checkpoint_path: str | None      # guest path, soc-sw-sim
```

Upload limits unchanged (8 MiB / 32 MiB). Paths must stay under
`work_mount`. Kernel/rootfs images larger than that belong on
`soc-models` or a work-drive image, not envd.

### 4.2 Client and Job

```python
class Client:
    def create_job(self, spec: JobSpec) -> Job: ...
    def get_job(self, job_id: str) -> Job: ...
    def list_jobs(self) -> list[Job]: ...

class Job:
    def wait(self, timeout_secs: int | None = None) -> JobResult: ...
    def collect(self, dest: Path) -> Path: ...
    def close(self) -> None: ...
    def run(self, command: str, timeout_secs: int | None = None) -> JobResult: ...
    def write_text(self, remote_path: str, content: str) -> None: ...
    def read_text(self, remote_path: str) -> str: ...
    # soc-sw-sim
    def start_emulator(self) -> None: ...
    def checkpoint(self, name: str = "default") -> str: ...
    def restore(self, name: str = "default") -> None: ...
    def console_log(self) -> str: ...          # read serial file
    def gdb(self, gdb_command: str) -> JobResult: ...  # in-guest gdb-multiarch
```

`create_job`:

1. Resolve workload → allowed templates, RO drive id/mount.
2. Validate timeout and uploads.
3. `POST /sandboxes` with `templateID` (no `attachedDrives`, no `fork`).
4. Fail closed if the RO drive is missing or not read-only.
5. `mkdir -p` `work_out` and, for SoC, `ckpt_dir`.
6. Upload `spec.upload`.
7. Return `Job` without starting the sim/emulator.

`rtl-sim` `wait()` runs `command` under `workdir`.

`soc-sw-sim` `wait()` if `command` is set: run that command (demo
Makefiles). Otherwise `start_emulator()` and wait until the serial log
matches a ready pattern or timeout.

`start_emulator()` (P0 QEMU RISC-V virt, illustrative):

```bash
qemu-system-riscv64 -machine virt -nographic -accel tcg \
  -bios /mnt/soc-models/opensbi.elf \
  -kernel /mnt/work/Image \
  -append 'console=ttyS0' \
  -serial file:/mnt/work/out/console.log \
  -gdb tcp:127.0.0.1:1234 \
  -netdev user,id=net0 -device virtio-net-device,netdev=net0 \
  -monitor unix:/mnt/work/qemu-monitor.sock,server,nowait
```

The SDK must refuse `-netdev tap`, `-enable-kvm`, and gdbstub binds other
than `127.0.0.1`.

`checkpoint(name)`: QEMU `migrate "exec:cat > /mnt/work/ckpt/<name>"` (or
equivalent `migrate` to file). Returns the guest path. Does **not** call
AgentENV pause.

`restore(name)`: stop emulator if needed; start with `-incoming` from that
file. Used after a **new** sandbox create or after pause/resume only when
the emulator is not already in RAM.

`gdb()`: `gdb-multiarch -batch -ex "target remote 127.0.0.1:1234" …`
via envd. No `/proxy`.

P0 does not call `POST /sandboxes/{id}/fork`.

### 4.3 CLI

```text
aenv sim templates [--workload rtl-sim|soc-sw-sim]
aenv sim run --workload rtl-sim --template chip-sim-8c --upload ./rtl:/mnt/work/rtl -- make -C /mnt/work sim
aenv sim run --workload soc-sw-sim --template chip-sw-sim-4c --upload ./Image:/mnt/work/Image
aenv sim checkpoint <job-id> [name]
aenv sim restore <job-id> [name]
aenv sim console <job-id>
aenv sim collect <job-id> ./out
aenv sim rm <job-id>
```

## 5. Guest layouts

RTL:

```
/mnt/pdk/
/mnt/work/rtl  tb  out/sim.log  out/*.vcd
```

SoC:

```
/mnt/soc-models/          # RO: dtb, opensbi, machine descriptions
/mnt/work/
  Image, rootfs.ext4      # optional uploads
  qemu-monitor.sock
  ckpt/<name>             # emulator native checkpoint
  out/console.log
  out/dmesg.txt           # optional
  out/core                # optional
```

## 6. Images

| Image | Workload | Role |
|---|---|---|
| `chip-sim-base` | rtl-sim | Ubuntu + Verilator + Yosys + cocotb |
| `chip-sim-pdk-sky130` | rtl-sim | RO extra drive |
| `chip-sw-sim-base` | soc-sw-sim | Ubuntu + qemu-system-riscv64 + Renode + gdb-multiarch |
| `chip-sw-sim-soc-models-rv64` | soc-sw-sim | RO extra drive: virt machine firmware/dtb |
| `chip-sim-work-empty` | both | RW extra drive, default 32 GiB |

## 7. P1 sidecar (not in P0)

Existing AgentENV hook URLs. Interprets
`customExtensionParams.chipSim.workloadType`. Loopback only.

P1 SoC extras (SDK, not AgentENV):

- Guest `websockify` (or equivalent) from a chosen WS port to
  `127.0.0.1:1234`.
- Agent connects with `/proxy` + `x-agentenv-target-port` = that WS port.
- SDK returns an opaque `DebugEndpoint` (sandbox id + port + kind), never
  a raw host:port of gdbstub.

## 8. Example workflows

### RTL single job

1. `Client.create_job(JobSpec(workload=RTL_SIM, command="make -C /mnt/work sim", upload={...}))`
2. `job.wait()` → collect `out/sim.log`

### SoC boot + serial

1. `create_job(JobSpec(workload=SOC_SW_SIM, upload={"/mnt/work/Image": Path("Image")}))`
2. `job.start_emulator()`
3. poll `job.console_log()` until login/panic/timeout
4. `job.collect(dest)`

### SoC checkpoint without re-boot

1. After a booted emulator: `path = job.checkpoint("after-login")`
2. `job.close()` or AgentENV pause
3. New job (or resume): `job.restore("after-login")` — nested kernel does
   not go through full boot
4. If the **same** sandbox was only paused, resume already restores QEMU
   RAM; `restore()` is for a fresh tools sandbox or a crashed emulator

## 9. Repository layout

```
docs/src/vertical/chip-sim/
  design.md
  interfaces.md
  test-plan.md

examples/chip-sim/                      # shared SDK + RTL demos
  config/chip-sim.toml
  python/chip_sim/                      # one package, both workloads
  python/tests/
  images/base/                          # RTL toolchain
  images/pdk-sky130/
  images/work-empty/
  demos/single-job/
  demos/regression/
  scripts/publish_snapshots.sh

examples/chip-sw-sim/                   # SoC images + demos only
  images/base/Dockerfile                # QEMU + Renode
  images/soc-models-rv64/README.md
  demos/riscv-virt-linux/               # Demo 1: serial capture
  demos/qemu-checkpoint/                # Demo 2: save/restore
  scripts/publish_snapshots.sh
```

`examples/chip-sw-sim` must import `chip_sim` from `examples/chip-sim/python`.
Do not copy the client.

AgentENV `src/`, `storage/`, and `services/` stay untouched in P0/P1.
