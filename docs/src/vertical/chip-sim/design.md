# Chip-Sim Vertical: Design

Status: **Approved baseline** (value, gates, and decision rules).
SDK internals may still iterate. Implementation follows this document.

A pretty architecture is not the product. Value is a chip **LLM agent**
closing **generate → simulate → read feedback → iterate** with isolation
and reproducibility. Human engineers and the AgentENV platform team are
secondary beneficiaries.

Related docs:

- [Interfaces and repository layout](./interfaces.md)
- [Test plan](./test-plan.md)

## 1. Why this exists

This project is **not** a simulator and **not** a replacement for a
production EDA farm. It is an **agent-facing simulation execution layer**
on AgentENV: the missing piece between “generic sandbox” and “chip agent
loop”.

| Audience | Priority | What they get |
|---|---|---|
| LLM chip agent | **Primary** | Short iteration; stable, isolated envs; one API for RTL and SoC software sim; PDK/model sharing; license control; failure artifacts |
| Human chip engineer | Secondary | Small-scale debug and replay of agent bugs — not 7×24 product regression |
| AgentENV platform | Constraint | Simulation is a **workload on** sandboxes. Kernel changes only when a business loop is blocked |

Compared with alternatives:

| Option | Gap for an LLM agent |
|---|---|
| Traditional EDA farm | Built for humans; no sub-second env snapshots; weak isolation for untrusted generated code |
| Raw AgentENV | Generic sandbox; agent must invent PDK drives, license, artifacts, SoC checkpoints |
| Homegrown sim cluster | Heavy; no agent-friendly snapshot/API |

### 1.1 Goals

1. An execution base for chip LLM agents: RTL hardware sim **and** SoC
   software sim in one closed loop.
2. Prove that AgentENV can host that load with **minimal kernel change**.
3. Failure scenes must be reproducible; artifacts persist; resources and
   licenses stay isolated and controllable.
4. Ship runnable examples, an Agent API, and docs the agent can call
   directly.

Success is **not** “Verilator/QEMU demo boots in a Firecracker VM”.
Success is the agent getting sim feedback and changing the next patch
without a human assembling drives, ports, and logs.

### 1.2 Non-goals

- Replace the company’s production EDA regression cluster or take 7×24
  full-product farms.
- Implement any simulator core (Verilator, QEMU, VCS, Simics stay
  external). This repo only schedules and wraps environments.
- Human GUI in P0/P1. Agent API first; CLI is a thin debug wrapper and
  **never** an acceptance criterion at any phase.
- Full commercial EDA as a P0/P1 must-have. Commercial tools are
  connectivity PoCs only.

### 1.3 Pseudo-value (reject in review)

1. Hand-running a sim sample in a sandbox with no agent loop.
2. “All tools” in phase one (VCS + Simics + licenses + anti-VM) while the
   agent loop still does not close.
3. A heavy homegrown job scheduler that reimplements AgentENV.
4. Early AgentENV kernel patches that raise upgrade risk for every
   sandbox tenant.
5. Treating a human-facing product regression farm as the primary goal.

## 2. Design choices → pain → value

Every technical move must answer: **what agent pain does this remove?**

| Design | Pain if we skip it | Value |
|---|---|---|
| Vertical layer; no AgentENV kernel change unless blocked | Kernel regressions and upgrade drag hit every sandbox product | Fast sim-layer iteration; platform stays stable |
| OCI + overlaybd RO extra drive for PDK / SoC models | Tens of GB recopied per job; slow start; disk blow-up | On-demand load + host page cache; concurrent agents share lowers |
| Template snapshots for warm start | Rebuilding the EDA/QEMU image every iteration (minutes) | Env attach in milliseconds; more agent loops per hour |
| Two snapshot layers (sandbox memory vs emulator checkpoint) | Pause vs SoC-state confusion; lost debug scenes | Idle host CPU without losing a portable nested-machine scene |
| customExtension license + idle-release | Agents stampede FlexLM; random job death | Bounded checkout; pause does not thrash licenses |
| One SDK, `rtl-sim` and `soc-sw-sim` | Two stacks, two error/artifact stories | One generate→sim→feedback API |
| RW extra drive for workspace / VCD / dumps | Rootfs too small; artifacts die with the VM | Agent still has logs after sandbox teardown |
| SDK TTL/resource checks; multi-spec snapshots | Warm start cannot override CPU/RAM | Large sims without a kernel patch |
| Pull artifacts on success **and** crash; never stop-hook-only | Panic/TTL kill drops VCD/console; agent has nothing to learn from | Every terminal state yields feedback or an explicit `artifacts_lost` |

## 3. Decisions (locked), with value tradeoffs

| # | Decision | Value tradeoff |
|---|---|---|
| 1 | P0 RTL: Verilator / Yosys / cocotb only. VCS later PoC. | Commercial license/anti-VM work delays the agent loop. Close the loop first. |
| 2 | Agent SDK first. CLI wraps the same client. | This product exists for chip LLM agents. A human-first CLI spends the budget on the wrong user. |
| 3 | P0 PDK: public Sky130. | Avoids foundry NDAs so we can prove overlaybd sharing quickly. |
| 4 | No AgentENV CPU/RAM override. Multi-spec snapshots. | Kernel edits risk the whole platform. Revisit P2 only if snapshot cardinality is measured as too costly. |
| 5 | P0 SoC: QEMU (RISC-V virt first) + Renode. Simics later PoC. | Same as (1): do not inflate scope before the loop works. |
| 6 | P0 debug: **serial file + in-sandbox gdb API**. No raw proxy ports. P1 may add a WS bridge with opaque handles. | Firmware agents need inspect/feedback. They do **not** need to see Firecracker proxy headers or gdbstub TCP. |

Additional locks (platform risk, not features):

- P0 does not use `fork` (batch-create instead).
- P0 has no custom extension (no licenses yet).
- P2 kernel patches are **not** a launch gate.
- Nested KVM unavailable → QEMU **TCG only**.
- Nested TAP forbidden → QEMU **user-net only**.
- SoC models on `/mnt/soc-models`, not a PDK drive.

## 4. Workloads (what the agent actually runs)

| | `rtl-sim` | `soc-sw-sim` |
|---|---|---|
| Agent writes | Verilog + testbench | Firmware, drivers, kernel patches |
| Tools (P0) | Verilator, Yosys, cocotb | QEMU, Renode, gdb-multiarch |
| Shared RO drive | `/mnt/pdk` Sky130 | `/mnt/soc-models` dtb/bootrom |
| Feedback | sim.log, VCD | console.log, dmesg, gdb text |
| Loop | Edit RTL → rebuild sim | Edit firmware → boot or restore checkpoint |

One client. The agent must not learn two sandbox stacks.

## 5. Architecture (how the loop is hosted)

Pain: raw AgentENV gives create/exec/files, not “run this RTL” or
“restore this SoC crash”.

```
Chip LLM agent ──► chip_sim.Client ──► AgentENV HTTP / envd
                       │
                       ├── rtl-sim snapshots:     chip-sim-{2c,8c,32c}
                       ├── soc-sw-sim snapshots:  chip-sw-sim-{2c,4c,8c}
                       ├── RO extra drive:        /mnt/pdk or /mnt/soc-models
                       ├── RW extra drive:        /mnt/work
                       └── artifacts (envd; object store in P1)
```

Warm start from a **pre-baked** alias. Cold start is the operator recipe
that publishes that alias, not the agent hot path.

## 6. Runtime facts the agent loop depends on

Checked in-tree. Wrong assumptions here destroy the “reproducible
failure” value.

### 6.1 Fork (unused in P0)

Overlaybd COW on extra drives (RO shared; RW sealed lower + new upper).
P0 still batch-creates: fork pauses the source, multiplies P1 licenses,
stays on one node, and inherits dirty work.

### 6.2 Read-only drives are host-enforced

Firecracker `is_read_only` + overlaybd absent upper. Guest mount may omit
`-o ro`. SDK fails closed if the RO drive is missing or writable — PDK
integrity is an agent-isolation requirement.

### 6.3 TTL

Node `default_sandbox_timeout_secs` (15) is a **default**, not a max.
Refresh is capped at 3600s. SDK always sends an explicit timeout so a
long sim is not killed before the agent reads the log.

### 6.4 Stop hooks are best-effort

Pause/delete/Drop may never deliver `stop`. Artifact value requires SDK
pull on every terminal path (§8).

### 6.5 No COPY/ADD in `aenv build`

Large trees go on extra-drive images so the agent hot path stays upload
of **small diffs**, not PDK copies.

### 6.6 Custom extension is HTTP-only

P0: none. P1: `127.0.0.1` sidecar. In-process hooks would be a kernel
change — rejected as pseudo-value.

### 6.7 Two snapshot layers (reproducible SoC failures)

**Pain:** treating AgentENV pause as “QEMU died, nested Linux is gone”
**or** as “pause is a portable SoC checkpoint” both break agent replay.

AgentENV pause **does** snapshot Firecracker guest RAM. The same sandbox
resume restores QEMU and nested SoC RAM. GDB/network sessions drop.

| Layer | Agent value | Cost |
|---|---|---|
| Pause / snapshot **while QEMU runs** | Idle the host; continue the **same** job | ≈ sandbox `memoryMB` |
| Template snapshot **before QEMU** | Fast empty tools env | Small; **no** nested OS |
| QEMU/Renode file on `/mnt/work/ckpt` | Replay a crash on a **new** tools sandbox | Nested RAM only |

Agent API: `Job.checkpoint()` is the portable failure scene. Pause is
host-resource management, not the crash-dump format.

### 6.8 Two networks

Sandbox `allowOut` = QEMU **process** (git/license). Nested NIC = QEMU
**user-net only**. No nested TAP/KVM. Agents do not get a high-perf
virtio-tap farm; they get a safe, isolated firmware loop.

### 6.9 Proxy is HTTP/WS, not GDB TCP

**Pain:** leaking `x-agentenv-target-port` into the agent prompt is
unsafe and unusable for gdb remote protocol.

P0 value: `Job.console_log()` and `Job.gdb(...)` (in-guest). P1 may add
opaque WS debug handles. Never teach the agent raw `/proxy` + gdbstub.

## 7. License (P1) — keep the loop stable under concurrency

Pain: N agents × pause/resume = FlexLM storms.

`interactive`: hold license; prefer emulator checkpoint over pause.
`batch`: pause allowed; release after `licenseIdleReleaseSecs` (default
60s). Keyed by `(sandboxId, sandboxInstanceId)`.

## 8. Artifacts — the agent’s only teacher

If the sim dies and logs vanish, the loop has **zero** value.

| | `rtl-sim` | `soc-sw-sim` |
|---|---|---|
| Happy / fail | `out/sim.log`, VCD | `out/console.log`, dmesg, coredump |
| Portable scene | n/a | `ckpt/**` |
| Crash / TTL | envd pull or `artifacts_lost` | same |

P0 pulls on `wait`/`close`. Object store + tail: P1.

## 9. Agent loops (the product)

### RTL

Warm-start `chip-sim-8c` → upload generated Verilog → `wait()` → parse
`sim.log` → patch → repeat. Many cases: N jobs, no fork.

### SoC software

Warm-start `chip-sw-sim-4c` → upload firmware → `start_emulator()` →
read `console_log` / `gdb` → `checkpoint()` on panic → collect → next
patch, optionally `restore()` so boot is not paid every iteration.

## 10. Phases: delivery **and** value proof

Standing footnote for **every** phase: the human CLI is a thin wrapper
only — it is **not** an acceptance criterion. This project does **not**
replace a production EDA farm.

### P0 — vertical only, zero kernel change

**Value:** prove the architecture; close the **minimum** agent loop
without touching upstream AgentENV. Find real blockers from that loop,
not from a tool matrix.

**Tech:** shared `chip_sim` SDK; Sky130 + RISC-V QEMU examples;
multi-spec snapshots; serial file; in-guest gdb API. All P0/V0/V1 logic
lives in this repo’s **vertical layer**. It does **not** modify upstream
AgentENV kernel code under `src/`, `storage/`, or `services/`.

**V0 value gate (hard P0 pass — no other substitute):**

An automated script calls `chip_sim.Client` and completes **two** full
agent iterations:

1. Iteration 1: submit RTL (or firmware) input → start the sim
   environment → collect sim logs/artifacts.
2. Iteration 2: patch that input from iteration-1 output → submit again
   → collect a new artifact set.

Also required: Sky130 RO extra drive mounted for the RTL path; SoC
emulator checkpoint on disk restores without a full nested boot.

`examples/` trees are **human debug aids**. Running them by hand **cannot**
prove P0. Only the automated SDK script counts.

Hand-running Verilator/QEMU under `aenv exec` does **not** count.

**Other P0 proofs:** V1 (no kernel path edits), V2 (RTL + SoC artifacts
via Client), V4 (crash still yields logs or `artifacts_lost`).

### P1 — agent-facing semantics

**Value:** hide sandbox/drive/proxy. License jitter, debug handles, and
artifact export become business APIs so the agent never speaks AgentENV.

**Tech:** sidecar `workloadType`; license idle TTL; WS debug handles;
checkpoint up/download; object-store collect.

**Proof:** an agent (or recorded agent script) runs a job through
`chip_sim` only; checkpoint replay works.

License: implement **pause delayed-release** so frequent
checkout/release jitter is bounded. This does **not** require a full
license audit trail or reporting dashboard (that is a later, derived
track).

### P2 — optional kernel patch

**Value:** only if measured snapshot-cardinality cost blocks the agent
loop. Not a feature checklist.

**P2 may start only if at least one measured bar is met:**

- The business resource matrix requires **≥ 10** snapshot aliases, and
  image maintenance cost is documented as unacceptable; **or**
- Agent load tests show that switching resource specs via many snapshots
  adds storage/pull latency that **materially slows** the generate→sim
  loop.

No such data → **no** AgentENV kernel edit. “Snapshots are annoying”
in conversation is not evidence.

### P3 — concurrent agent farm (still not a product EDA cluster)

**Value:** many agents in parallel; failed jobs replayable; artifacts
complete. Still not “replace the EDA farm”.

**Proof:** N concurrent jobs; a failed job’s env/artifacts suffice to
reproduce without the original sandbox remaining running.

## 11. Remaining gaps (upper layer only)

| Gap | P0 handling | Why not kernel |
|---|---|---|
| Remote GDB TCP | In-guest `Job.gdb`; serial file | Proxy is HTTP/WS; TCP proxy is a new platform surface |
| Nested TAP / KVM | Forbidden; TCG + user-net | Firecracker guest has no nested KVM product path |
| Portable SoC state | Emulator checkpoint files | Pause already restores **same** sandbox RAM |

## 12. Value-decay risks

1. Kernel edits before the agent loop exists → launch blocked, no value.
2. Commercial tool sprawl in P0 → loop never closes.
3. Demos without an SDK-only closed loop → architecture unproven.
4. Homegrown scheduler/storage → duplicate AgentENV, higher maintenance.
5. Positioning as “next-gen full EDA cluster” → wrong SLAs, wrong users.

## 13. Change-review checklist (required on every design/code PR)

A PR that cannot answer these is rejected:

1. Which **concrete agent-loop** problem does this change solve? (No
   answer → reject.)
2. Can it live in this repo’s vertical layer? Must AgentENV kernel
   change? Without the P2 evidence bars in §10, kernel edits are
   forbidden.
3. Is this P0 loop-blocking, or a P1/P2/P3 enhancement? Do not smuggle
   later work into an early milestone.

## 14. Development order and FakeAgentEnv

Approved baseline covers **positioning, value gates, acceptance, and
decision rules**. It does **not** freeze SDK internals.

Start order (mandatory):

1. **FakeAgentEnv** — in-process mock of sandbox / extra drive / proxy.
   Lets `chip_sim` business logic run as local unit tests **without**
   Firecracker. It does **not** replace later live integration tests.
2. **SDK Client** against that mock, including the V0 two-iteration
   script.
3. Live integration (KVM) only after V0 is green on the mock.

Forbidden as the main verification path: hand-run QEMU/Verilator demos.
`examples/` may appear later as **debug aids**, never as P0 proof.

FakeAgentEnv must **not** be an ideal world. It has to reproduce
AgentENV failure modes the SDK will see on Firecracker: TTL expiry,
pause destroying guest processes (emulator RAM gone unless a file
checkpoint exists), RO extra-drive writes rejected, drive mount
failures, envd unreachable on collect. Happy-path-only mocks are
rejected in review.

The V0 script must include a **failing** sim iteration that still
returns parseable logs/artifacts. An all-green loop is not the agent’s
real job.

When `examples/` files are added, each entrypoint must start with:

> This example is a human debug aid only. It is not stage acceptance.
> P0/Pn acceptance is the automated `chip_sim.Client` V0 script.

SDK records lightweight metrics (distinct snapshot aliases used,
sandbox start latency). These exist so a future P2 kernel discussion
has numbers, not anecdotes.

New features and issues must pass the §13 three questions **in
discussion before** a code PR. Code PRs that skip that review are
rejected.

## 15. P0 implementation non-goals (tactical)

Commercial job support, GUI, GPU/PCIe/dongles, nested KVM/TAP, AgentENV
source changes, in-process hooks, fork in demos, raw TCP forwarding.
Human CLI is never an acceptance criterion. This project does not
replace a production EDA farm.
