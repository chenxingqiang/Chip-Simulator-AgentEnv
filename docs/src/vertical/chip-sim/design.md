# Chip-Sim Vertical: Design

Status: **Design** (pending human approval before implementation)

A layer above AgentENV for two chip workloads that share storage, TTL,
license, and artifact machinery. AgentENV runtime changes stay optional.

Related docs:

- [Interfaces and repository layout](./interfaces.md)
- [Test plan](./test-plan.md)

## 1. Goal

Give LLM agents (and a thin CLI wrapper) two job kinds on the same SDK:

| `workloadType` | What it simulates | P0 tools |
|---|---|---|
| `rtl-sim` | Verilog hardware (RTL + testbench) | Verilator, Yosys, cocotb |
| `soc-sw-sim` | Virtual SoC running firmware / OS / drivers | QEMU (RISC-V virt first), Renode |

Neither path modifies AgentENV. They differ in images, extra drives, and
upper-layer semantics (waveforms vs serial/GDB/SoC checkpoints).

## 2. Decisions (locked)

| # | Question | Decision |
|---|---|---|
| 1 | RTL toolchain (P0) | Open-source only: Verilator, Yosys, cocotb, pytest. Commercial VCS is a later connectivity PoC. |
| 2 | Interaction | One Agent-facing SDK. Human `aenv sim …` wraps that SDK. No second implementation. |
| 3 | RTL PDK (P0) | Public Sky130. Internal foundry PDKs replace the extra-drive image later. |
| 4 | Hot-start CPU/memory override | **Do not change AgentENV.** Publish resource-specific snapshots per workload. |
| 5 | SoC tools (P0) | Open-source QEMU (RISC-V virt first) and Renode. Simics is a later connectivity PoC, not a P0 job type. |
| 6 | Debug in P0 | **Serial to file** is required. **GDB runs inside the sandbox** via envd against a localhost gdbstub. No remote TCP GDB, no port allocator. A WebSocket-to-TCP bridge through `/proxy` is P1. |

Additional locks:

- P0 does **not** use `fork`. Regressions batch-create sandboxes.
- P0 does **not** require a custom extension (no licenses).
- P2 AgentENV patches are **not** a launch gate.
- In-process lifecycle hooks are **out of scope**. P1, if needed, is a
  localhost HTTP sidecar.
- Nested KVM is unavailable. QEMU inside the sandbox is **TCG only**.
- QEMU networking is **user-net (SLIRP) only**. No TAP/TUN for the nested NIC.
- SoC machine models live on a read-only extra drive (`/mnt/soc-models`),
  not a PDK drive.

## 3. Workload comparison

| | `rtl-sim` | `soc-sw-sim` |
|---|---|---|
| Object | Verilog hardware | Pre-built C SoC model + software stack |
| Inputs | RTL, testbench, PDK | ELF, U-Boot, kernel, dtb, rootfs |
| CPU shape | Saturated, minutes–hours | Burst + idle on I/O |
| Outputs | VCD/FSDB, sim log | Serial/console, dmesg, coredump |
| Checkpoint that matters | AgentENV snapshot of the **tool** environment | QEMU/Renode **machine** checkpoint on `/mnt/work` (see §5.7) |
| Agent loop | Edit RTL → rebuild sim | Edit firmware → boot or restore SoC |

## 4. Shared architecture

```
Chip agent ──► chip_sim.Client ──► AgentENV HTTP / envd
                    │
                    ├── rtl-sim snapshots:     chip-sim-{2c,8c,32c}
                    ├── soc-sw-sim snapshots:  chip-sw-sim-{2c,4c,8c}
                    ├── RO extra drive:        /mnt/pdk  or  /mnt/soc-models
                    ├── RW extra drive:        /mnt/work
                    └── artifacts via envd (+ object store in P1)
```

| Mount | `rtl-sim` | `soc-sw-sim` |
|---|---|---|
| User rootfs | Verilator / Yosys / cocotb | QEMU / Renode / gdb-multiarch |
| RO extra drive | `/mnt/pdk` (Sky130) | `/mnt/soc-models` (dtb, bootrom, machine JSON) |
| RW extra drive | RTL, TB, VCD, logs | kernel/elf, QEMU ckpt, serial log, coredump |

Resource variants still use **multiple snapshots**, not a runtime CPU
override. Cold-start once per alias, then warm-start jobs from that alias.

## 5. Runtime facts that affect both workloads

Checked in-tree. The vertical must not assume otherwise.

### 5.1 Fork and extra drives (COW, unused in P0)

Fork pauses the source, restacks overlaybd uppers, resumes the source, then
starts children from that snapshot.

- RO drives: shared lowers, no restack.
- RW work: sealed upper becomes a shared lower; each child gets a new upper.
- P0 still avoids fork (source pause, `start-resume` license multiplication
  in P1, same-node only, dirty work inherited). OpenAPI `count` max is 100.

### 5.2 `readOnly` extra drives are host-enforced

1. Overlaybd uses `ResolvedUpperMode::Absent` (no writable upper).
2. Firecracker sets virtio-blk `is_read_only`.

Guest init mounts extra drives without `-o ro`. That is hardening, not the
security boundary. The SDK fails closed if the RO drive is missing or
writable.

### 5.3 TTL is a default, plus one real cap

`default_sandbox_timeout_secs` (15) applies only when create/resume omits
`timeout`. Create / `POST /timeout` have no documented max besides `int32`.
`POST /sandboxes/{id}/refreshes` **maximum is 3600**.

SDK policy: always send an explicit timeout (default 1 h, max 24 h);
long jobs use `/timeout`, not refreshes-as-only-keepalive.

### 5.4 Stop hooks are best-effort

`stop` runs on pause, delete, and Drop. It is not a reliable artifact
exporter. Collection policy is §7.

### 5.5 Template build cannot COPY/ADD

Small files: envd. Large trees (RTL, kernels, SoC models): extra-drive
images.

### 5.6 Custom extension is HTTP-only

`[custom_extension].url`. P0 ships none. P1 is `127.0.0.1`.

### 5.7 Two snapshot layers (SoC — do not confuse them)

AgentENV pause is **not** “kill the VM and drop RAM”. Pause captures
Firecracker guest memory (`process_vm_readv` → overlaybd memory layers),
stops the VMM, and releases the netns. Resume restores that memory. A
QEMU process inside the sandbox **is restored**, including nested SoC RAM.

| Layer | What it stores | Restores nested Linux/RTOS? | Cost |
|---|---|---|---|
| AgentENV pause / committed snapshot taken **while QEMU is running** | Entire Firecracker guest RAM + disks | **Yes**, same sandbox (or snapshot clone) | ≈ sandbox `memoryMB` (e.g. 8 GiB) plus disk deltas |
| AgentENV template snapshot taken **before QEMU starts** | Toolchain only | **No** | Small |
| QEMU/Renode native checkpoint on `/mnt/work` | Nested machine only | **Yes**, on a **fresh** tools sandbox | Nested RAM (often tens–hundreds of MiB) |

Use each layer on purpose:

- Same sandbox, idle the host: AgentENV pause/resume is valid. GDB and
  nested network sessions drop; the SoC CPU/RAM come back.
- Survive **delete**, a new tools sandbox, or a cheap portable artifact:
  QEMU/Renode checkpoint to `/mnt/work/ckpt/`. **This is the SoC job
  API.** Do not tell agents that pause replaces it.
- Do not take an 8 GiB Firecracker memory snapshot just to save a 256 MiB
  RISC-V guest. Checkpoint the emulator, then pause or delete.

Required SoC flow before pause-that-will-be-followed-by-a-new-sandbox or
delete:

1. `Job.checkpoint()` → file on `/mnt/work`.
2. Optional AgentENV pause (releases host CPU; file remains on the RW drive).
3. Resume or new job: start QEMU with `-incoming` / Renode load from that file.

### 5.8 Two networks and no nested TAP

| Plane | Role | Mechanism |
|---|---|---|
| Sandbox egress | QEMU/Renode **process** → git, license | `network.allowOut` / `denyOut` |
| Nested guest NIC | Linux/RTOS inside QEMU | **QEMU user-net only** (`-netdev user,id=net0`) |

Firecracker already uses a host TAP for the sandbox `eth0`. The vertical
must not create a nested TAP for QEMU:

- Nested `/dev/kvm` is not provided; QEMU is TCG.
- Nested TAP/TUN is unsupported for this product. Even if the guest kernel
  has `CONFIG_TUN`, do not bridge QEMU onto it.
- User-net is SLIRP: enough for DHCP-like outbound from the nested OS,
  not a high-performance virtio-tap farm.

### 5.9 Proxy is HTTP / SSE / WebSocket, not raw TCP

`/proxy` forwards HTTP and WebSocket to `x-agentenv-target-port`. It does
**not** forward gdb remote protocol or telnet.

Consequences (locks decision 6):

- P0 serial: QEMU `-serial file:/mnt/work/out/console.log` (and/or stdio
  captured by envd). Agent reads the file.
- P0 GDB: QEMU `-gdb tcp:127.0.0.1:1234` and `gdb-multiarch` **inside**
  the sandbox via `Job.run`. The agent never opens host TCP:1234.
- P1 optional: a guest WebSocket-to-TCP bridge (e.g. serial or gdbstub)
  so an external debugger can use `/proxy` WebSocket. Port allocation
  stays in the SDK; AgentENV is unchanged.

## 6. License and pause (P1)

Same policy for VCS and Simics. P0 has no FlexLM.

| Mode | Pause | License |
|---|---|---|
| `interactive` | Avoid pause; keep sandbox or use emulator checkpoint + short pause | Hold until job end or idle TTL |
| `batch` | Pause allowed after emulator checkpoint | Release after `licenseIdleReleaseSecs` (default 60s) |

```json
{
  "chipSim": {
    "workloadType": "soc-sw-sim",
    "mode": "interactive",
    "project": "cpu-core",
    "soc": "rv64-virt",
    "pdk": "sky130",
    "licenseFeatures": [],
    "licenseIdleReleaseSecs": 60
  }
}
```

Sidecar keys checkout by `(sandboxId, sandboxInstanceId)`.

## 7. Artifact collection

Never rely on stop-hook-only export.

| Path | `rtl-sim` | `soc-sw-sim` |
|---|---|---|
| Happy path | `/mnt/work/out/**` (sim.log, VCD) | `/mnt/work/out/**` (console.log, dmesg, coredump) |
| Checkpoint files | n/a | `/mnt/work/ckpt/**` collected when present |
| Unclean / TTL | envd pull or `artifacts_lost` | same |
| Long job tail (P1) | `sim.log` | `console.log` |

P0: collect on `wait`/`close`. Object store and incremental tail: P1.

## 8. Agent loops

### 8.1 RTL (`rtl-sim`)

Warm-start `chip-sim-8c` → upload RTL → `make sim` → parse log → iterate.
Regression: N × `create_job`, no fork.

### 8.2 SoC software (`soc-sw-sim`)

1. Warm-start `chip-sw-sim-4c`.
2. Upload kernel/elf (small) or mount them from `/mnt/soc-models`.
3. Start QEMU: serial to `console.log`, gdbstub on `127.0.0.1:1234`, user-net.
4. Agent reads console via files API; optional in-guest gdb via `Job.run`.
5. `Job.checkpoint()` writes `/mnt/work/ckpt/<id>`.
6. Pause or delete the sandbox (checkpoint file stays on the RW drive if
   the sandbox is only paused; copy out with `collect` if deleting).
7. Continue: resume (QEMU still in RAM) **or** new sandbox + QEMU restore
   from the checkpoint file (no full nested boot).
8. Regression: batch-create sandboxes, one firmware case each.

## 9. Phases

### P0 — zero runtime changes

RTL (`examples/chip-sim`):

- Images + Sky130 drive + aliases `chip-sim-{2c,8c,32c}`.
- Demos: adder; batch regression (no fork).

SoC (`examples/chip-sw-sim`):

- QEMU/Renode image + `soc-models` drive + aliases `chip-sw-sim-{2c,4c,8c}`.
- Demo 1: RISC-V virt, prebuilt mini Linux, capture serial to file.
- Demo 2: QEMU checkpoint save/restore, no full reboot.
- No Simics. No remote GDB. No TAP.

Shared Python package `chip_sim` with `WorkloadType`.

### P1 — sidecar + ports + artifacts

- `workloadType` in sidecar params (`rtl-sim` / `soc-sw-sim`).
- License idle TTL (VCS + Simics connectivity).
- SoC: WebSocket-to-TCP bridge + SDK port handles; checkpoint upload/download
  helpers.
- Object-store sink + log tail.
- Fork probe only; regressions still batch-create.

### P2 — optional AgentENV patches

Unchanged: multi-snapshot first; warm-start resource override only with
evidence. Guest extra-drive `-o ro`. Resource-aware scheduler.

### P3 — job API

One `Job` resource with `workloadType`. CLI remains a wrapper.

## 10. Gap list

RTL gaps from the previous review still apply (TTL policy, fork, license
jitter, multi-spec snapshots). SoC adds only upper-layer gaps:

| Gap | Handling |
|---|---|
| Remote GDB / telnet | P0: in-guest gdb + serial file. P1: WS bridge over `/proxy`. No kernel TCP proxy. |
| Nested TAP/TUN | Forbidden. QEMU user-net. |
| Nested `/dev/kvm` | Unavailable. QEMU TCG. RISC-V-on-x86 is TCG anyway. |
| SoC run-state portability | QEMU/Renode checkpoint on `/mnt/work`. AgentENV pause restores in-place only. |

## 11. Non-goals (P0)

- Commercial EDA or Simics job support.
- GPU / PCIe / dongles / nested KVM / nested TAP.
- Modifying AgentENV orchestrator, Firecracker, overlaybd, or scheduler.
- In-process hooks.
- Cross-node fork; `fork` in regression demos.
- Raw TCP port forwarding through AgentENV.
