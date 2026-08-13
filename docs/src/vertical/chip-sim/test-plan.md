# Chip-Sim Vertical: Test Plan

Cases for [design.md](./design.md) and [interfaces.md](./interfaces.md).
P0 tests are written **before** SDK code (TDD).

P0 is **not** done when QEMU/Verilator runs under `aenv exec`. It is done
when a script that uses **only** `chip_sim.Client` completes two
iterations (code → sim → log → patched code → sim) and collects
artifacts, with no AgentENV kernel changes. That is value gate **V0**
below; L*/S*/D* support it, they do not replace it.

Shared unit tests: `examples/chip-sim/python/tests/`.
SoC demo/live tests may live next to `examples/chip-sw-sim/demos/`.
No `/dev/kvm` unless tagged `live`.

## 0. Value gates

Standing footnote: the human CLI is **never** an acceptance criterion.
This project does not replace a production EDA farm. `examples/` hand
runs do **not** prove P0.

**FakeAgentEnv:** in-process mock of sandbox / extra drive / proxy so
`chip_sim` unit tests run without Firecracker. It must reproduce
failures (TTL, pause killing processes, RO writes, mount errors, envd
down), not only happy paths. It does **not** replace live KVM tests.

| ID | Gate | Pass |
|---|---|---|
| V0 | Agent loop, SDK only (hard P0 bar) | Automated script calls `chip_sim.Client` for **two** iterations: (1) submit RTL or firmware → sim → collect logs; (2) patch from iteration-1 output → sim again → new artifacts. **At least one iteration must be a failing sim** that still returns parseable logs (not an all-green loop). No raw `attachedDrives` JSON. Sky130 RO drive mounted on RTL path; SoC checkpoint restore works without full nested boot. Hand `examples/` runs do not count. |
| V1 | Vertical layer only | P0/V0/V1 logic is implemented in this repo’s vertical layer. Diff vs main does **not** modify upstream AgentENV kernel code under `src/`, `storage/`, or `services/`. |
| V2 | RTL + SoC both feedback | Client-collected artifacts for RTL and SoC paths (D1/D3 via Client, not hand demo) |
| V3 | Portable SoC scene | Checkpoint restore skips full nested boot (via Client) |
| V4 | Failure still teaches | Forced kill/unreachable envd → `artifacts_lost` or pulled logs, never silent empty |

## 1. P0 unit tests (no KVM)

### Config and policy

| ID | Case | Expected |
|---|---|---|
| C1 | Default TOML | RTL default `chip-sim-8c`; SoC default `chip-sw-sim-4c` |
| C2 | Omitted `timeout_secs` | HTTP `timeout: 3600`, never node default 15s |
| C3 | `timeout_secs > max` | Refused before HTTP |
| C4 | `timeout_secs == 0` | Refused |
| C5 | Unknown template | Refused with allowed list for that workload |
| C6 | RTL template used with `soc-sw-sim` | Refused |
| C7 | SoC template used with `rtl-sim` | Refused |
| C8 | Env `CHIP_SIM_WORKLOAD` | Selects workload default template |

### Upload limits

| ID | Case | Expected |
|---|---|---|
| U1 | File ≤ 8 MiB, total ≤ 32 MiB | Allowed |
| U2 | Single file > 8 MiB | Refused: pack into extra-drive image |
| U3 | Total > 32 MiB | Same |
| U4 | Path outside work mount | Refused |
| U5 | SoC kernel via upload ≤ 8 MiB | Allowed; larger Image must come from `soc-models` |

### Job state machine (FakeAgentEnv)

| ID | Case | Expected |
|---|---|---|
| J1 | RTL `create_job` | `POST /sandboxes` + RTL `templateID`; no drives; no fork |
| J2 | RTL PDK writable or missing | Fail closed |
| J3 | SoC `create_job` | SoC `templateID`; RO drive id `soc-models` |
| J4 | SoC models drive writable or missing | Fail closed |
| J5 | RTL `wait` | `cd workdir && command` |
| J6 | Command success / non-zero | Collect; SUCCEEDED / FAILED; artifacts kept on failure |
| J7 | envd down on `close` | `artifacts_lost` or LOST |
| J8 | `close` idempotent | No-op |
| J9 | `create_job` does not start sim/QEMU | Starts on `wait` / `start_emulator` |
| J10 | `keep_sandbox=true` | No delete |
| J11 | SoC `start_emulator` args | Contains `-accel tcg`, `file:…/console.log`, `gdb tcp:127.0.0.1:1234`, `-netdev user`; **rejects** tap and `-enable-kvm` |
| J12 | `checkpoint("after-login")` | Guest path `/mnt/work/ckpt/after-login`; **no** AgentENV pause |
| J13 | `restore` on fake QEMU | Starts with incoming/load from that path |
| J14 | `gdb("info registers")` | envd command using `target remote 127.0.0.1:1234`; **no** `/proxy` |
| J15 | `console_log` | Reads serial file via files API |

### Artifacts

| ID | Case | Expected |
|---|---|---|
| A1 | RTL `out/sim.log` | Collected |
| A2 | SoC `out/console.log` | Collected |
| A3 | SoC `ckpt/` present | Collected with logs |
| A4 | `out/` missing | Created at job start; empty collect is not lost |
| A5 | Collect after delete | `artifacts_lost` |

### CLI

| ID | Case | Expected |
|---|---|---|
| CLI1 | `aenv sim run --workload rtl-sim …` | `Client.create_job` + `wait` |
| CLI2 | `aenv sim run --workload soc-sw-sim …` | Same client, SoC spec |
| CLI3 | `aenv sim checkpoint` / `restore` / `console` | Job methods only |

## 2. P0 live tests (`live`)

### RTL

| ID | Case | Expected |
|---|---|---|
| L1 | Adder on `chip-sim-8c` | Verilator/cocotb pass; `sim.log` |
| L2 | Warm vs cold start | Warm much faster (record both) |
| L3 | `/mnt/pdk` not writable | `touch` fails |
| L4 | `/mnt/work` writable | Demo writes `out/` |
| L5 | 4 parallel RTL jobs | No fork; four sandboxes |
| L6 | Timeout 120s | Survives past 15s |
| L7 | Alias 2c/8c resources | Match interfaces table |

### SoC

| ID | Case | Expected |
|---|---|---|
| S1 | RISC-V virt mini Linux | `console.log` contains kernel boot banner |
| S2 | `/mnt/soc-models` not writable | `touch` fails |
| S3 | QEMU user-net only | Nested guest can egress only as SLIRP allows; no `/dev/net/tun` use by demo |
| S4 | No `/dev/kvm` in guest | QEMU still runs (TCG) |
| S5 | Checkpoint then new sandbox restore | Serial shows post-checkpoint state without full boot |
| S6 | AgentENV pause/resume **same** sandbox with QEMU running | QEMU still alive after resume (memory snapshot); serial continues; document dropped gdb sockets |
| S7 | Pause **without** QEMU checkpoint, then **delete**, new sandbox | Nested state gone (tools template only) — proves file checkpoint is required for portability |
| S8 | In-guest gdb `info registers` | Succeeds against localhost stub |
| S9 | `/proxy` to gdbstub port | HTTP/WS handshake fails or is non-gdb — documents no raw TCP |
| S10 | Batch 3 firmware cases | N creates, no fork |

S6 vs S7 is the two-layer snapshot teaching test.

## 3. P0 demos

| ID | Demo | Pass |
|---|---|---|
| D1 | `examples/chip-sim/demos/single-job` | `make sim`, local `sim.log` |
| D2 | `examples/chip-sim/demos/regression` | ≥3 cases, zero fork |
| D3 | `examples/chip-sw-sim/demos/riscv-virt-linux` | Serial capture of mini Linux |
| D4 | `examples/chip-sw-sim/demos/qemu-checkpoint` | Restore skips full boot |

## 4. Runtime facts (probes)

| ID | Fact | Check |
|---|---|---|
| R1 | Node timeout 15s is a default | L6 |
| R2 | Refresh max 3600 | SDK must not rely on it alone |
| R3 | Fork extra drives COW | P1 probe |
| R4 | Guest extra-drive mount may look rw | Writes still fail (L3/S2) |
| R5 | COPY/ADD unsupported | RTL/SoC images not built via `aenv build` COPY |
| R6 | Pause captures Firecracker guest RAM | S6 |
| R7 | Proxy is not raw TCP | S9 |
| R8 | Nested KVM absent | S4 |

## 5. P1 cases (not now)

| ID | Case | Expected |
|---|---|---|
| P1-1 | Sidecar loopback only | Non-loopback refused |
| P1-2–P1-5 | License idle TTL / instance id | Pause delayed-release bounds checkout jitter. **Not** a license audit/reporting product. |
| P1-6 | Object-store put | After `wait` |
| P1-7 | Crash before stop hook | envd pull or `lost` |
| P1-8 | Tail `console.log` / `sim.log` | Chunks while running |
| P1-9 | Fork probe | COW; demos still no fork |
| P1-10 | WS-to-TCP gdb through `/proxy` | External gdb-over-WS works; SDK hides port |
| P1-11 | Simics license connectivity | Checkout/release only, not a job type |

## 6. Out of scope

- AgentENV `make test` suites.
- Simics as a P0 workload.
- Nested KVM, nested TAP, GPU.
- Warm-start resource override (P2) unless §10 evidence bars in design.md are met.
- Raw TCP forwarding in AgentENV.
- Human CLI as any milestone’s pass bar.
- Replacing a production EDA farm.

## 7. TDD order

1. FakeAgentEnv mock (sandbox/drive/proxy), no Firecracker.
2. Config / `JobSpec` (C*, U*) including `WorkloadType`.
3. SDK Client against FakeAgentEnv (J*, A*).
4. Automated V0 two-iteration script on the mock, **including a failing
   sim that still collects logs** (**required** before demos).
5. CLI wrapper last among unit work; **not** a pass bar.
6. Live KVM: RTL L* then SoC S* / V2 / V3 / V4.
7. V1 checked at PR time (no `src/` / `storage/` / `services/` edits).

Hand QEMU/Verilator demos are debug-only and must not lead development.

Every later feature PR must answer the three questions in
[design.md](./design.md) §13.

No production module without a failing test from this list.
