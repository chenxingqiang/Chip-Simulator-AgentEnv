# Chip-Sim Vertical: Design

Status: **Design** (pending human approval before implementation)

This document is the approved-direction design for adding a **chip-simulation
vertical** on top of AgentENV. It incorporates the architecture review and
records facts verified against the current AgentENV runtime.

Related docs:

- [Interfaces and repository layout](./interfaces.md)
- [Test plan](./test-plan.md)

## 1. Goal

Give LLM agents (and a thin human CLI wrapper) a reproducible way to:

1. Boot a ready EDA environment in milliseconds from a snapshot.
2. Attach a shared read-only PDK and an isolated writable workspace.
3. Run open-source RTL simulation (Verilator / Yosys / cocotb).
4. Collect logs and artifacts even when the sandbox dies uncleanly.
5. Scale simple regressions by creating many sandboxes, not by forking.

The vertical is a **layer above AgentENV**. AgentENV runtime changes are
optional and postponed.

## 2. Decisions (locked)

| # | Question | Decision |
|---|---|---|
| 1 | Toolchain for P0 | Open-source only: Verilator, Yosys, cocotb, pytest. Commercial VCS is a later connectivity PoC, not a P0 regression farm. |
| 2 | Interaction | Agent-facing SDK first (E2B-style exec/files + AgentENV-native create). Human `aenv sim …` is a thin wrapper over the same SDK, not a second implementation. |
| 3 | PDK | P0 uses public Sky130. Internal foundry PDKs replace the extra-drive image later. |
| 4 | Hot-start CPU/memory override | **Do not change AgentENV.** Publish multiple resource snapshots (`chip-sim-2c`, `chip-sim-8c`, `chip-sim-32c`). Revisit a runtime patch only if snapshot cardinality becomes the bottleneck. |

Additional locks from review:

- P0 does **not** use `fork`. Regressions batch-create sandboxes.
- P0 does **not** require a custom extension (no licenses).
- P2 AgentENV kernel patches are **not** a launch gate.
- In-process lifecycle hooks are **out of scope**. AgentENV only supports an
  external HTTP custom extension; a true in-process hook would be a runtime
  change. P1, if needed, is a **localhost sidecar**.

## 3. Architecture

```
Chip agent ──► chip-sim SDK ──► AgentENV HTTP / envd
                    │
                    ├── template/snapshot: chip-sim-{2c,8c,32c}
                    ├── extra drive pdk  (read-only overlaybd)
                    ├── extra drive work (writable overlaybd)
                    └── artifacts: envd files + object store (P1+)
```

Each job is one sandbox:

| Mount | Content | Backing |
|---|---|---|
| `/` (user rootfs) | Toolchain snapshot | Template rootfs |
| `/mnt/pdk` | Sky130 (or later internal PDK) | Read-only extra drive |
| `/mnt/work` | RTL, testbench, logs, VCD | Writable extra drive |

### 3.1 Why this split

- Toolchain changes slowly → bake into snapshots.
- PDK is large, sensitive, and shared → read-only extra drive, overlaybd
  lower layers + host page cache.
- Workspace is per-job and dirty → isolated writable upper.

### 3.2 Resource variants without a runtime patch

Warm start (`POST /sandboxes` with `templateID`) inherits CPU, memory, disk,
and attached drives from the snapshot. Cold start can set `cpuCount` /
`memoryMB` / `attachedDrives`.

P0 build recipe (once per resource class):

1. Cold-start the toolchain image with the desired `cpuCount` / `memoryMB`.
2. Attach Sky130 (RO) and an empty work image (RW).
3. Verify tools (`verilator --version`, `yosys -V`).
4. Ensure `/mnt/work` is empty.
5. `POST /snapshots` → alias `chip-sim-8c` (etc.).
6. Jobs warm-start that alias.

## 4. Runtime facts that affect the vertical

These were checked in-tree; the vertical must not assume otherwise.

### 4.1 Fork and extra drives (COW, still unused in P0)

Fork pauses the source, restacks overlaybd uppers (`close_seal + restack`),
resumes the source, then starts children from that snapshot config.

- Read-only PDK: no restack; children share the same lowers.
- Writable work: the sealed upper becomes a shared lower; **each child gets
  its own new writable upper**. This is overlaybd COW, not a full disk copy.
- Memory follows the same snapshot-share model as other resumes.

P0 still avoids fork:

- Source is paused (and pause fires the custom-extension **stop** hook).
- Every child fires `start-resume` (license checkout would multiply in P1).
- Same node only. OpenAPI `count` maximum is 100; the concept doc still says
  16 — treat 100 as the API cap, not a farm scheduler.
- A dirty parent work drive is inherited as a lower by every child.
- Extra-drive fork is not the first thing we want to debug in P0.

P1 may add a **fork probe** (see the test plan). Large regressions stay on
batch create.

### 4.2 `readOnly` extra drives are host-enforced

`attachedDrives.readOnly=true` is not only a guest mount flag:

1. Overlaybd materialization uses `ResolvedUpperMode::Absent` — no writable
   upper.
2. Firecracker `PUT /drives/{id}` sets `is_read_only` on the virtio-blk
   device.

Guest init (`tools-image/init`) currently mounts extra drives **without**
`-o ro`. A guest `remount,rw` cannot make a Firecracker read-only virtio-blk
writable. That missing `-o ro` is hardening, not the security boundary.

P0 requirement: the SDK **must** pass `readOnly: true` for PDK drives and
refuse to start if the create response / snapshot metadata shows the PDK
drive as writable.

Optional later hardening (not P0): guest `-o ro` via extra boot args or a
tools-drive change.

### 4.3 TTL is a default, plus one real cap

`orchestrator.default_sandbox_timeout_secs` (default 15) is used **when the
create/resume request omits `timeout`**. It is not a silent max clamp on an
explicit timeout.

Real limits:

- Create / resume / `POST /timeout`: `timeout >= 0`, no documented maximum
  besides `int32`.
- `POST /sandboxes/{id}/refreshes` duration **maximum is 3600 seconds**.

Chip-sim policy (enforced in the SDK, not by hoping the node clamps):

- Always send an explicit job timeout (default 1 hour, max 24 hours unless
  the operator config raises it).
- Long jobs use `POST /timeout` or periodic timeout refresh, **not** the
  3600-capped refreshes endpoint as the only keepalive.
- Fail create if the requested timeout exceeds the SDK policy max.

### 4.4 Stop hooks are best-effort

Custom-extension `stop` runs on pause, delete, and Drop, and is best-effort
(failures are logged). It is **not** a reliable artifact exporter:

- VM panic / ublk death / host crash: hook may never run.
- TTL `autoPause: true` (default) pauses rather than deletes; pause still
  stops the VM and fires `stop`.
- TTL `autoPause: false` deletes; still best-effort.

Artifact policy is in §6.

### 4.5 Template build cannot COPY/ADD

`aenv build` rejects `COPY` and `ADD`. Small files go through envd
(`aenv upload` / E2B files API). Large RTL trees and PDKs are extra-drive
images, never file-API copies.

### 4.6 Custom extension cannot be in-process without a runtime change

The only integration is `[custom_extension].url` → HTTP hooks. P0 ships no
extension. P1 is a sidecar on `127.0.0.1`, started next to the node.

## 5. License and pause (P1, recorded now)

P0 has no FlexLM. The policy below is binding for P1 so we do not paint
into a corner.

**Modes**

| Mode | When | Pause | License |
|---|---|---|---|
| `interactive` | Agent short iteration | Avoid pause. Prefer snapshot for checkpoints. Keep the sandbox running (or extend TTL). | Hold until job end or idle TTL. |
| `batch` | Regression / long sim | Pause is allowed to free CPU/memory. | Release on pause **after** `license_idle_release_secs` (default 60s), not immediately. Resume re-checkouts. |

`customExtensionParams` (opaque to AgentENV):

```json
{
  "chipSim": {
    "mode": "interactive",
    "project": "cpu-core",
    "pdk": "sky130",
    "licenseFeatures": [],
    "licenseIdleReleaseSecs": 60
  }
}
```

The sidecar keys checkout state by `(sandboxId, sandboxInstanceId)`. A
pause `stop` for an old instance must not release a newer instance's
license.

## 6. Artifact collection

Never rely on stop-hook-only export.

| Path | Trigger | Mechanism |
|---|---|---|
| Happy path | Command exit 0/non-0 | SDK pulls `/mnt/work/out/**` via envd files API, then optional object-store put (P1). |
| Unclean stop | SDK sees sandbox not Running | Same pull while the filesystem is still reachable; if not, mark artifacts `lost`. |
| TTL pause | auto-evict pause | Treat as unclean; pull before or immediately after pause if the paused snapshot still exposes files (P1 may snapshot-then-export). |
| Long job | periodic | SDK tails `/mnt/work/out/sim.log` on an interval. |

P0 implements happy-path + best-effort pull on SDK `close()`. Object store
and incremental tail land in P1.

## 7. Phases

### P0 — zero runtime changes

- Toolchain OCI image and Sky130 extra-drive image recipes.
- Snapshot aliases `chip-sim-2c` / `chip-sim-8c` / `chip-sim-32c`.
- Python SDK: create from alias, upload small RTL, exec sim, collect
  `/mnt/work/out`.
- Demos: single-job adder; batch regression via N creates (no fork).
- Thin CLI wrapping the SDK.

### P1 — sidecar + artifacts + fork probe

- Localhost custom-extension sidecar (license idle TTL, optional VPN).
- Object-store artifact sink + log tail.
- Documented fork probe on a tiny work drive; regressions still batch-create.

### P2 — optional AgentENV patches (not a launch gate)

Only after production evidence:

- Warm-start `cpuCount` / `memoryMB` override.
- Guest extra-drive `-o ro`.
- Resource-aware scheduler.

### P3 — job API

Stable `Job` resource in the SDK (and optional HTTP facade). CLI remains a
wrapper. No second code path.

## 8. Non-goals (P0)

- Commercial EDA, GPU / PCIe / nested KVM / dongles.
- Modifying AgentENV orchestrator, Firecracker, overlaybd, or scheduler.
- In-process hooks.
- Cross-node fork.
- Using `fork` in the regression demo.
