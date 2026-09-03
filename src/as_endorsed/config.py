from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Paths and knobs. Override any field with an AS_ENDORSED_* environment variable."""

    model_config = SettingsConfigDict(env_prefix="AS_ENDORSED_", env_file=".env", extra="ignore")

    data_dir: Path = REPO_ROOT / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"

    @property
    def synthetic_dir(self) -> Path:
        return self.data_dir / "synthetic"


settings = Settings()
