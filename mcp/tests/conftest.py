import pytest
from src.config import Config
from src.deps import Deps


@pytest.fixture
def config(tmp_path):
    return Config(state_root=tmp_path / "state", read_roots=(tmp_path,), write_roots=(tmp_path,))


@pytest.fixture
def deps(config):
    return Deps(config)
