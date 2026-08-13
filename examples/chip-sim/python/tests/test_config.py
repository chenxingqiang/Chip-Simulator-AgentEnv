from pathlib import Path

import pytest

from chip_sim.config import load_config
from chip_sim.errors import ConfigError
from chip_sim.types import WorkloadType

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "chip-sim.toml"


def test_c1_default_templates(config):
    assert config.workload(WorkloadType.RTL_SIM).default_template == "chip-sim-8c"
    assert config.workload(WorkloadType.SOC_SW_SIM).default_template == "chip-sw-sim-4c"


def test_c8_env_workload_selects_default(monkeypatch, config):
    monkeypatch.setenv("CHIP_SIM_WORKLOAD", "soc-sw-sim")
    assert config.resolve_workload(None) == WorkloadType.SOC_SW_SIM


def test_unknown_workload_env(monkeypatch):
    monkeypatch.setenv("CHIP_SIM_WORKLOAD", "not-a-workload")
    cfg = load_config(CONFIG_PATH)
    with pytest.raises(ConfigError):
        cfg.resolve_workload(None)
