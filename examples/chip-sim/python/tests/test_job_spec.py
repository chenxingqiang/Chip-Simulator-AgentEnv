from pathlib import Path

import pytest

from chip_sim.errors import PolicyError
from chip_sim.types import JobSpec, WorkloadType


def test_c3_timeout_above_max_refused(client):
    spec = JobSpec(
        workload=WorkloadType.RTL_SIM,
        command="true",
        timeout_secs=90_000,
    )
    with pytest.raises(PolicyError, match="timeout"):
        client.create_job(spec)


def test_c4_timeout_zero_refused(client):
    spec = JobSpec(workload=WorkloadType.RTL_SIM, command="true", timeout_secs=0)
    with pytest.raises(PolicyError, match="timeout"):
        client.create_job(spec)


def test_c5_unknown_template_refused(client):
    spec = JobSpec(
        workload=WorkloadType.RTL_SIM,
        command="true",
        template="not-a-template",
    )
    with pytest.raises(PolicyError, match="allowed"):
        client.create_job(spec)


def test_c6_rtl_template_rejected_for_soc(client):
    spec = JobSpec(
        workload=WorkloadType.SOC_SW_SIM,
        template="chip-sim-8c",
        command="true",
    )
    with pytest.raises(PolicyError):
        client.create_job(spec)


def test_c7_soc_template_rejected_for_rtl(client):
    spec = JobSpec(
        workload=WorkloadType.RTL_SIM,
        template="chip-sw-sim-4c",
        command="true",
    )
    with pytest.raises(PolicyError):
        client.create_job(spec)


def test_u2_single_file_over_8mib_refused(client, tmp_path: Path):
    big = tmp_path / "too-big.v"
    big.write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    spec = JobSpec(
        workload=WorkloadType.RTL_SIM,
        command="true",
        upload={"/mnt/work/rtl/too-big.v": big},
    )
    with pytest.raises(PolicyError, match="extra-drive"):
        client.create_job(spec)


def test_u4_path_outside_work_refused(client, tmp_path: Path):
    f = tmp_path / "a.v"
    f.write_text("module x; endmodule\n")
    spec = JobSpec(
        workload=WorkloadType.RTL_SIM,
        command="true",
        upload={"/etc/passwd": f},
    )
    with pytest.raises(PolicyError, match="work"):
        client.create_job(spec)
