from pathlib import Path

import pytest

from chip_sim.errors import ReadOnlyDriveError, SandboxError
from chip_sim.fake import FakeAgentEnv
from chip_sim.types import JobSpec, JobState, WorkloadType


def test_c2_omitted_timeout_sends_3600(client, fake: FakeAgentEnv):
    spec = JobSpec(workload=WorkloadType.RTL_SIM, command="true")
    job = client.create_job(spec)
    rec = fake.sandbox(job.sandbox_id)
    assert rec.timeout_secs == 3600
    assert rec.template_id == "chip-sim-8c"


def test_j1_rtl_create_uses_template_no_fork(client, fake: FakeAgentEnv):
    spec = JobSpec(workload=WorkloadType.RTL_SIM, command="true")
    job = client.create_job(spec)
    rec = fake.sandbox(job.sandbox_id)
    assert rec.template_id == "chip-sim-8c"
    assert rec.fork_count == 0
    assert fake.last_create_attached_drives is None


def test_j2_rtl_pdk_writable_fails_closed(client, fake: FakeAgentEnv):
    fake.force_ro_writable = True
    spec = JobSpec(workload=WorkloadType.RTL_SIM, command="true")
    with pytest.raises(SandboxError, match="read-only"):
        client.create_job(spec)


def test_j3_soc_create_uses_soc_models_drive(client, fake: FakeAgentEnv):
    spec = JobSpec(workload=WorkloadType.SOC_SW_SIM, command="true")
    job = client.create_job(spec)
    rec = fake.sandbox(job.sandbox_id)
    assert rec.template_id == "chip-sw-sim-4c"
    ids = {d.drive_id for d in rec.drives}
    assert "soc-models" in ids
    assert any(d.mount_path == "/mnt/soc-models" and d.read_only for d in rec.drives)


def test_j4_soc_models_missing_fails_closed(client, fake: FakeAgentEnv):
    fake.omit_ro_drive = True
    spec = JobSpec(workload=WorkloadType.SOC_SW_SIM, command="true")
    with pytest.raises(SandboxError, match="soc-models|read-only|missing"):
        client.create_job(spec)


def test_j9_create_does_not_start_sim(client, fake: FakeAgentEnv):
    spec = JobSpec(workload=WorkloadType.RTL_SIM, command="make -C /mnt/work sim")
    job = client.create_job(spec)
    rec = fake.sandbox(job.sandbox_id)
    assert rec.exec_count == 0
    assert rec.emulator_running is False


def test_j11_start_emulator_flags(client):
    spec = JobSpec(workload=WorkloadType.SOC_SW_SIM)
    job = client.create_job(spec)
    argv = job.emulator_argv()
    joined = " ".join(argv)
    assert "-accel" in argv and "tcg" in argv
    assert "file:/mnt/work/out/console.log" in joined
    assert "tcp:127.0.0.1:1234" in joined
    assert "-netdev" in argv and "user" in joined
    with pytest.raises(ValueError, match="tap|kvm"):
        job.emulator_argv(extra=("-enable-kvm",))
    with pytest.raises(ValueError, match="tap|kvm"):
        job.emulator_argv(extra=("-netdev", "tap,id=n0"))


def test_j12_checkpoint_does_not_pause(client, fake: FakeAgentEnv):
    spec = JobSpec(workload=WorkloadType.SOC_SW_SIM)
    job = client.create_job(spec)
    job.start_emulator()
    path = job.checkpoint("after-login")
    assert path == "/mnt/work/ckpt/after-login"
    rec = fake.sandbox(job.sandbox_id)
    assert rec.paused is False
    assert rec.pause_count == 0


def test_readonly_pdk_write_rejected(client, fake: FakeAgentEnv):
    spec = JobSpec(workload=WorkloadType.RTL_SIM, command="true")
    job = client.create_job(spec)
    with pytest.raises(ReadOnlyDriveError):
        fake.write_file(job.sandbox_id, "/mnt/pdk/x", b"nope")


def test_pause_kills_emulator_process(client, fake: FakeAgentEnv):
    spec = JobSpec(workload=WorkloadType.SOC_SW_SIM)
    job = client.create_job(spec)
    job.start_emulator()
    fake.pause(job.sandbox_id)
    rec = fake.sandbox(job.sandbox_id)
    assert rec.paused is True
    assert rec.emulator_running is False


def test_ttl_expiry(client, fake: FakeAgentEnv):
    spec = JobSpec(workload=WorkloadType.RTL_SIM, command="true", timeout_secs=1)
    job = client.create_job(spec)
    fake.expire_ttl(job.sandbox_id)
    result = job.run("true")
    assert result.state == JobState.LOST
    assert "ttl" in result.stderr.lower() or "expired" in result.stderr.lower()


def test_j7_envd_down_sets_artifacts_lost(client, fake: FakeAgentEnv, tmp_path: Path):
    spec = JobSpec(workload=WorkloadType.RTL_SIM, command="true")
    job = client.create_job(spec)
    fake.envd_up = False
    result = job.close()
    assert result.artifacts_lost is True
    assert result.state in {JobState.LOST, JobState.CLOSED}


def test_j8_close_idempotent(client):
    spec = JobSpec(workload=WorkloadType.RTL_SIM, command="true")
    job = client.create_job(spec)
    job.close()
    job.close()
