from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]

_base_url = make_url(get_settings().database_url)
if not (_base_url.database or "").endswith("_test"):
    os.environ["DATABASE_URL"] = _base_url.set(database="lawteam_test").render_as_string(
        hide_password=False
    )
    get_settings.cache_clear()

from app.database import engine  # noqa: E402
from app.main import app  # noqa: E402

assert engine.url.database is not None and engine.url.database.endswith("_test"), (
    f"Refusing to run pytest on non-test database: {engine.url.database!r}"
)


@pytest.fixture(scope="session", autouse=True)
def ensure_test_db_schema() -> None:
    """Apply Alembic migrations once per session on the isolated *_test database."""
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", engine.url.render_as_string(hide_password=False))
    command.upgrade(cfg, "head")


@pytest.fixture
def isolated_cases_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect CASES_ROOT to a temporary directory for filesystem tests."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CASES_ROOT", str(cases_dir))
    get_settings.cache_clear()
    yield cases_dir
    get_settings.cache_clear()


@pytest.fixture
def client(isolated_cases_root: Path) -> Iterator[TestClient]:
    with TestClient(app) as tc:
        yield tc
