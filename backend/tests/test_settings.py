"""Settings loading: env vars, options.json, precedence."""

import json
from pathlib import Path

from app.core.settings import Settings


def test_env_vars_load() -> None:
    s = Settings.load({"FF_ENV": "dev", "FF_LOG_LEVEL": "DEBUG"})
    assert s.env == "dev"
    assert s.log_level == "DEBUG"


def test_database_url_defaults_into_data_dir(tmp_path: Path) -> None:
    s = Settings.load({"FF_DATA_DIR": str(tmp_path)})
    assert s.resolved_database_url == f"sqlite:///{(tmp_path / 'family_finance.db').as_posix()}"


def test_explicit_database_url_wins(tmp_path: Path) -> None:
    s = Settings.load({
        "FF_DATA_DIR": str(tmp_path),
        "FF_DATABASE_URL": "sqlite:///custom.db",
    })
    assert s.resolved_database_url == "sqlite:///custom.db"


def test_options_file_merges(tmp_path: Path) -> None:
    (tmp_path / "options.json").write_text(json.dumps({"log_level": "WARNING"}))
    s = Settings.load({"FF_DATA_DIR": str(tmp_path)})
    assert s.log_level == "WARNING"


def test_env_beats_options_file(tmp_path: Path) -> None:
    (tmp_path / "options.json").write_text(json.dumps({"log_level": "WARNING"}))
    s = Settings.load({"FF_DATA_DIR": str(tmp_path), "FF_LOG_LEVEL": "DEBUG"})
    assert s.log_level == "DEBUG"


def test_secrets_not_serialised() -> None:
    s = Settings.load({"FF_SUPERVISOR_TOKEN": "dev-supervisor-token"})
    assert "dev-supervisor-token" not in s.model_dump_json()
