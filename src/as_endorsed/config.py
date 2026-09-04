from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Paths and knobs. Override any field with an AS_ENDORSED_* environment variable."""

    model_config = SettingsConfigDict(env_prefix="AS_ENDORSED_", env_file=".env", extra="ignore")

    data_dir: Path = REPO_ROOT / "data"
    llm_model: str = "claude-opus-5"
    llm_enabled: bool = True  # only takes effect when credentials and the anthropic package are present
    embedder: str = "bge"  # bge | hash
    reranker: str = "minilm"  # minilm | bge | none

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"

    @property
    def synthetic_dir(self) -> Path:
        return self.data_dir / "synthetic"

    @property
    def endorse_dir(self) -> Path:
        return self.data_dir / "endorse"

    @property
    def resolved_dir(self) -> Path:
        return self.data_dir / "resolved"


settings = Settings()
