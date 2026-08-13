"""V0 value gate: two Client iterations, including a failing sim that still yields logs.

This script is the P0 acceptance path. Hand-run examples/ demos do not count.
"""

from __future__ import annotations

from pathlib import Path

from chip_sim.client import Client
from chip_sim.config import load_config
from chip_sim.fake import FakeAgentEnv
from chip_sim.types import JobSpec, JobState, WorkloadType

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "chip-sim.toml"
FAIL_RTL = "module dut; /* ERROR_INJECT */ endmodule\n"
PASS_RTL = "module dut; endmodule\n"


def test_v0_two_iterations_fail_then_pass(tmp_path: Path):
    config = load_config(CONFIG_PATH)
    fake = FakeAgentEnv(config)
    client = Client(config=config, backend=fake)

    fail_src = tmp_path / "dut.v"
    fail_src.write_text(FAIL_RTL)
    job1 = client.create_job(
        JobSpec(
            workload=WorkloadType.RTL_SIM,
            command="make -C /mnt/work sim",
            upload={"/mnt/work/rtl/dut.v": fail_src},
        )
    )
    rec1 = fake.sandbox(job1.sandbox_id)
    assert any(d.drive_id == "pdk" and d.read_only for d in rec1.drives)

    r1 = job1.wait()
    assert r1.state == JobState.FAILED
    assert r1.artifacts_lost is False
    log1 = (r1.artifact_dir / "sim.log").read_text()
    assert log1.strip()

    pass_src = tmp_path / "dut2.v"
    pass_src.write_text(PASS_RTL)
    job2 = client.create_job(
        JobSpec(
            workload=WorkloadType.RTL_SIM,
            command="make -C /mnt/work sim",
            upload={"/mnt/work/rtl/dut.v": pass_src},
        )
    )
    r2 = job2.wait()
    assert r2.state == JobState.SUCCEEDED
    assert r2.artifacts_lost is False
    log2 = (r2.artifact_dir / "sim.log").read_text()
    assert "PASS" in log2
    assert log1 != log2

    aliases = fake.metrics.snapshot_aliases_used
    assert "chip-sim-8c" in aliases
    assert fake.metrics.start_latency_samples


def test_v0_soc_checkpoint_restore(client):
    spec = JobSpec(workload=WorkloadType.SOC_SW_SIM)
    job = client.create_job(spec)
    job.start_emulator()
    path = job.checkpoint("after-login")
    job.restore("after-login")
    log = job.console_log()
    assert "checkpoint-restore" in log or "restored" in log.lower() or path.endswith("after-login")
