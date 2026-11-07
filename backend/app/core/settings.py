"""Application settings.

Sources, in precedence order (highest last):
1. Defaults defined on :class:`Settings`
2. An HA add-on-style ``options.json`` file (``FF_OPTIONS_FILE`` or
   ``<data_dir>/options.json``)
3. Environment variables prefixed ``FF_``

Environment separation is via ``env`` ∈ {dev, ci, prod}; nothing reads
real financial data in non-prod environments.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "ci", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FF_", extra="ignore")

    _isolated: ClassVar[bool] = False  # tests only: ignore real env sources

    env: Environment = "prod"
    data_dir: Path = Path("/data")
    database_url: str | None = None  # default derived from data_dir below
    log_level: str = "INFO"
    ha_url: str = "http://supervisor/core"
    supervisor_token: SecretStr | None = None
    options_file: Path | None = None
    backup_dir: Path | None = None
    backup_keep: int = 7
    backup_interval_hours: float = 24.0  # 0 disables the scheduler
    # issue #70 job runner — scheduled feed sync cadence (Starling
    # allows ~1000 req/day; 4h is comfortably inside) and the tick
    # period the runner wakes on. 0 disables each.
    sync_interval_minutes: float = 240.0
    scheduler_tick_seconds: float = 60.0
    admin_token: SecretStr | None = None
    vault_key: SecretStr | None = None  # FF_VAULT_KEY; file fallback
    ingress: bool = False               # FF_INGRESS: addon mode

    @property
    def resolved_backup_dir(self) -> Path:
        return self.backup_dir or (self.data_dir / "backups")

    @property
    def restore_marker(self) -> Path:
        return self.data_dir / "restore.pending"

    @property
    def database_path(self) -> Path | None:
        """SQLite file behind ``resolved_database_url``, if file-based."""
        url = self.resolved_database_url
        if not url.startswith("sqlite:///"):
            return None
        path = Path(url.removeprefix("sqlite:///"))
        return None if path.name == ":memory:" else path

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        db = self.data_dir / "family_finance.db"
        return f"sqlite:///{db.as_posix()}"

    @classmethod
    def load(cls, environ: dict[str, str] | None = None) -> Settings:
        """Build settings, folding in options.json when present.

        ``environ`` overrides the real environment (tests); when given,
        real env vars are NOT consulted — the dict is the whole world.
        """
        raw = dict(os.environ) if environ is None else dict(environ)
        env_kwargs = {
            f: raw[f"FF_{f.upper()}"]
            for f in cls.model_fields
            if f"FF_{f.upper()}" in raw
        }
        opts_path = Path(
            env_kwargs.get("options_file")
            or Path(str(env_kwargs.get("data_dir", "/data"))) / "options.json"
        )
        options: dict[str, Any] = (
            json.loads(opts_path.read_text()) if opts_path.is_file() else {}
        )
        merged = {**options, **env_kwargs}
        if environ is None:
            return cls(**merged)
        prev = cls._isolated
        cls._isolated = True
        try:
            return cls(**merged)
        finally:
            cls._isolated = prev

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        if cls._isolated:
            return (init_settings,)
        return (init_settings, env_settings, dotenv_settings,
                file_secret_settings)


@lru_cache
def get_settings() -> Settings:
    return Settings.load()
