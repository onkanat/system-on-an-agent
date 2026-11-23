from __future__ import annotations

from pathlib import Path

import pytest

from soa.system import System

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _kosu_kaydi_izole(tmp_path_factory, monkeypatch):
    """Her test kendi koşu kaydı dizinine yazar — ev dizinine sızmasın."""
    monkeypatch.setenv("SOA_RUN_DIR", str(tmp_path_factory.mktemp("soa-runs")))


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def system(tmp_root: Path) -> System:
    return System(file_root=tmp_root)


@pytest.fixture
def dummy_addon_path() -> Path:
    return FIXTURES / "dummy_addon"
