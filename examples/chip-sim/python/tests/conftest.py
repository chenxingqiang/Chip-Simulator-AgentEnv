from pathlib import Path

import pytest

from chip_sim.client import Client
from chip_sim.config import load_config
from chip_sim.fake import FakeAgentEnv

CONFIG_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "chip-sim.toml"
)


@pytest.fixture
def config():
    return load_config(CONFIG_PATH)


@pytest.fixture
def fake(config):
    return FakeAgentEnv(config)


@pytest.fixture
def client(config, fake):
    return Client(config=config, backend=fake)
