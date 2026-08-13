from pathlib import Path

from chip_sim.types import JobSpec, JobState, WorkloadType


def _rtl(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "dut.v"
    p.write_text(body)
    return p


def test_failing_sim_still_collects_log(client, tmp_path: Path):
    rtl = _rtl(tmp_path, "module dut; /* ERROR_INJECT */ endmodule\n")
    spec = JobSpec(
        workload=WorkloadType.RTL_SIM,
        command="make -C /mnt/work sim",
        upload={"/mnt/work/rtl/dut.v": rtl},
    )
    job = client.create_job(spec)
    result = job.wait()
    assert result.state == JobState.FAILED
    assert result.exit_code != 0
    assert result.artifacts_lost is False
    log = (result.artifact_dir / "sim.log").read_text()
    assert "ERROR" in log or "fail" in log.lower()


def test_a2_out_created_even_if_empty(client, tmp_path: Path):
    spec = JobSpec(workload=WorkloadType.RTL_SIM, command="true")
    job = client.create_job(spec)
    dest = tmp_path / "out"
    job.collect(dest)
    assert dest.is_dir()
