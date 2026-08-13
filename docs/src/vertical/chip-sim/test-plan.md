# Chip-Sim Vertical: Test Plan

Natural-language cases for the design in [design.md](./design.md) and
[interfaces.md](./interfaces.md). P0 tests are written **before** SDK
code (TDD). Cases marked **code-verified** assert runtime facts we
already observed; they must not regress if we later touch AgentENV.

P0 implementation tests live in `examples/chip-sim/python/tests/`. They
must not require `/dev/kvm` unless tagged `live`.

## 1. P0 unit tests (no KVM)

### Config and policy

| ID | Case | Expected |
|---|---|---|
| C1 | Load default `chip-sim.toml` | `default_template=chip-sim-8c`, timeout 3600, max 86400, three aliases |
| C2 | `timeout_secs` omitted on `JobSpec` | SDK sends explicit `timeout: 3600`, never relies on node default 15s |
| C3 | `timeout_secs > max_timeout_secs` | Create refused before HTTP |
| C4 | `timeout_secs == 0` | Refused |
| C5 | Unknown template alias | Refused; list of allowed aliases in the error |
| C6 | Env `CHIP_SIM_TEMPLATE` | Overrides default template, still must be allowed |
| C7 | Env `CHIP_SIM_TIMEOUT_SECS` above max | Refused |

### Upload limits

| ID | Case | Expected |
|---|---|---|
| U1 | File ≤ 8 MiB, total ≤ 32 MiB | Allowed |
| U2 | Single file > 8 MiB | Refused with “pack into work extra-drive image” |
| U3 | Many small files totaling > 32 MiB | Same refusal |
| U4 | Upload path escapes `/mnt/work` | Refused |

### Job state machine (FakeAgentEnv)

| ID | Case | Expected |
|---|---|---|
| J1 | `create_job` | `POST /sandboxes` with `templateID`, explicit timeout, no `attachedDrives`, no `fork` |
| J2 | PDK drive in sandbox metadata is writable | Create fails closed |
| J3 | PDK drive missing | Create fails closed |
| J4 | `wait` runs `cd /mnt/work && <command>` | Exit code, stdout, stderr recorded |
| J5 | Command success | `collect` then `close`; `state=SUCCEEDED`; `artifacts_lost=false` |
| J6 | Command non-zero | Still `collect`; `state=FAILED`; artifacts present |
| J7 | envd unreachable on `close` | `state=LOST` or `artifacts_lost=true`; no exception swallowing the original error |
| J8 | `close` is idempotent | Second close is a no-op |
| J9 | `create_job` does not start the sim command | Command starts only on `wait`/`run` |
| J10 | `keep_sandbox=true` | Collects artifacts; does not delete sandbox |

### Artifacts

| ID | Case | Expected |
|---|---|---|
| A1 | `/mnt/work/out/sim.log` exists | Copied to `artifact_dir` |
| A2 | `out/` missing | Create `out/` at job start; empty collect is success, not lost |
| A3 | Glob does not match extra files outside `out/` | Not collected |
| A4 | Collect after delete | `artifacts_lost=true` |

### CLI wrapper

| ID | Case | Expected |
|---|---|---|
| CLI1 | `aenv sim run …` | Calls `Client.create_job` + `wait`; does not reimplement HTTP |
| CLI2 | Unknown template | Same error as SDK |

## 2. P0 live tests (KVM, tagged `live`)

Require a node with the three snapshots published.

| ID | Case | Expected |
|---|---|---|
| L1 | Single-job adder on `chip-sim-8c` | Verilator/cocotb pass; `sim.log` collected |
| L2 | Warm start latency | Sandbox Running well under cold-start time (record both) |
| L3 | `/mnt/pdk` is not writable | `touch /mnt/pdk/x` fails; overlaybd/Firecracker RO |
| L4 | `/mnt/work` is writable | Demo RTL runs and writes `out/` |
| L5 | Batch regression: 4 parallel `create_job` | No `fork` API calls; 4 independent sandboxes; all logs collected |
| L6 | Explicit timeout 120s | Job not evicted at 15s |
| L7 | Snapshot aliases 2c/8c | Inherited `cpuCount`/`memoryMB` match the table in interfaces.md |

L3 is the live counterpart of host-enforced `readOnly`.

## 3. P0 demos as tests

| ID | Demo | Pass criteria |
|---|---|---|
| D1 | `demos/single-job` | `make sim` in sandbox, `out/sim.log` locally after collect |
| D2 | `demos/regression` | `run_batch.py` with ≥3 cases, N sandboxes, zero fork requests |

## 4. Runtime facts (document / probe, not P0 blockers)

These protect the design against silent AgentENV changes.

| ID | Fact | How to check |
|---|---|---|
| R1 | `default_sandbox_timeout_secs` is a **default**, not a max | Cold/warm create with `timeout: 120` survives past 15s (L6) |
| R2 | Refresh endpoint max 3600 | Documented; SDK must not use it as the only long-job keepalive |
| R3 | Fork extra drives are overlaybd COW | P1 probe: write unique file in parent work, fork, child sees it; child write not visible to parent |
| R4 | Fork pause fires extension stop | P1 sidecar log |
| R5 | Guest extra-drive mount lacks `-o ro` | `findmnt` may show rw while writes still fail (L3) |
| R6 | `aenv build` rejects COPY/ADD | Existing CLI unit test; chip-sim must not depend on COPY |

## 5. P1 cases (not implemented now)

| ID | Case | Expected |
|---|---|---|
| P1-1 | Sidecar binds 127.0.0.1 only | Connection from non-loopback refused |
| P1-2 | `mode=interactive` pause | License **not** released until `licenseIdleReleaseSecs` |
| P1-3 | `mode=batch` pause after idle TTL | License released once |
| P1-4 | Pause stop then resume start-resume | New `sandboxInstanceId`; old stop ignored |
| P1-5 | Agent pause/resume storm | Checkout count bounded by idle TTL, not by pause frequency |
| P1-6 | Happy-path artifact put to object store | Object exists after `wait` |
| P1-7 | Crash before stop hook | SDK pull still gets logs if envd is up; else `lost` |
| P1-8 | Incremental tail | `sim.log` chunks appear locally while sim still runs |
| P1-9 | Fork probe (tiny work) | COW as R3; **regression demo still does not fork** |

## 6. Out of scope

- AgentENV unit/integration suites (`make test`, orchestrator tests).
- Commercial EDA license servers.
- GPU / nested KVM.
- Warm-start resource override (P2, optional).

## 7. Implementation order (TDD)

1. `test_config.py` / `test_job_spec.py` (C*, U*) → `config.py` types.
2. `test_client_fake.py` (J*, A*) → `Client` against FakeAgentEnv.
3. `test_cli.py` (CLI*) → `cli.py`.
4. Live L* / D* after snapshots exist.

No production module is added without a failing test from this list.
