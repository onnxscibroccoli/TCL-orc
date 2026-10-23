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
    # daemon
    socket: Path = Path(".tcl/daemon.sock")
    tasks_db: Path = Path(".tcl/tasks.db")
    http_host: str = "127.0.0.1"
    http_port: int = Field(8787, ge=0, le=65535)  # 0 disables the HTTP health server
    # zk / chain
    zk_build: Path = Path(".tcl/zk")
    zk_depth: int = Field(4, ge=1, le=32)
    ptau_power: int = Field(13, ge=8, le=28)
    rpc_url: str = "http://127.0.0.1:8545"
    # Anvil dev key #0 by default; set TCL_CHAIN_PRIVATE_KEY for anything non-local.
    chain_private_key: str = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    deployment: Path = Path(".tcl/deployment.json")

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
