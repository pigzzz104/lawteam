from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture
def isolated_cases_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect CASES_ROOT to a temporary directory for filesystem tests."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CASES_ROOT", str(cases_dir))
    get_settings.cache_clear()
    yield cases_dir
    get_settings.cache_clear()


@pytest.fixture
def client(isolated_cases_root: Path) -> TestClient:
    with TestClient(app) as tc:
        yield tc
