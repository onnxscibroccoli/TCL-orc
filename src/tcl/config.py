"""Runtime configuration (pydantic-settings).

Precedence (highest first): explicit kwargs > environment (TCL_*) > .env file >
config.yaml (path from TCL_CONFIG, default ./config.yaml) > field defaults.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import (
    BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict, YamlConfigSettingsSource,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TCL_", env_file=".env", extra="ignore")

    db: Path = Path("tcl.db")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "WARNING"
    mode: Literal["linear", "fixed"] = "fixed"
    tree_depth: int = Field(20, ge=1, le=64)
    worktree_root: Optional[Path] = None

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings
                                   ) -> tuple[PydanticBaseSettingsSource, ...]:
        yaml_path = Path(os.environ.get("TCL_CONFIG", "config.yaml"))
        sources = [init_settings, env_settings, dotenv_settings]
        if yaml_path.is_file():
            sources.append(YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path))
        return tuple(sources)


def get_settings(**overrides) -> Settings:
    return Settings(**{k: v for k, v in overrides.items() if v is not None})
