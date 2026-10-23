import sys
from pathlib import Path

import pytest
import yaml

PKG_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PKG_ROOT.parents[1]

sys.path.insert(0, str(PKG_ROOT / "tools"))


@pytest.fixture(scope="session")
def pkg_root() -> Path:
    return PKG_ROOT


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


def _load_yaml(name: str) -> dict:
    path = PKG_ROOT / "config" / "frozen" / name
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.fixture(scope="session")
def episodes_cfg() -> dict:
    return _load_yaml("episodes.yaml")


@pytest.fixture(scope="session")
def labels_cfg() -> dict:
    return _load_yaml("labels.yaml")


@pytest.fixture(scope="session")
def splits_cfg() -> dict:
    return _load_yaml("splits.yaml")


@pytest.fixture(scope="session")
def gates_cfg() -> dict:
    return _load_yaml("gates.yaml")


@pytest.fixture(scope="session")
def data_store_cfg() -> dict:
    return _load_yaml("data_store.yaml")


@pytest.fixture(scope="session")
def analysis_root(repo_root) -> Path:
    """Small-artifact tree; may not exist before X0."""
    return repo_root / "workspace" / "analysis" / "move_exhaustion_v2"
