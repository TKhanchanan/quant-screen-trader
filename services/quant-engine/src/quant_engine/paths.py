"""Cross-platform application data paths."""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APP_DIRECTORY_NAME = "QuantScreenTrader"
DATA_DIR_ENV = "QST_DATA_DIR"


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    database: Path
    market_data: Path
    models: Path
    logs: Path
    debug: Path
    exports: Path

    @property
    def database_file(self) -> Path:
        return self.database / "quant-screen-trader.sqlite3"

    @property
    def directories(self) -> tuple[Path, ...]:
        return (
            self.root,
            self.database,
            self.market_data,
            self.models,
            self.logs,
            self.debug,
            self.exports,
        )


def application_data_dir(
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Return a data root without embedding a username or host-specific path."""
    environment = os.environ if environ is None else environ
    override = environment.get(DATA_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()

    system_name = platform.system() if system is None else system
    user_home = Path.home() if home is None else home

    if system_name == "Darwin":
        return user_home / "Library" / "Application Support" / APP_DIRECTORY_NAME
    if system_name == "Windows":
        roaming = environment.get("APPDATA", "").strip()
        base = Path(roaming) if roaming else user_home / "AppData" / "Roaming"
        return base / APP_DIRECTORY_NAME

    xdg_data_home = environment.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg_data_home) if xdg_data_home else user_home / ".local" / "share"
    return base / APP_DIRECTORY_NAME


def app_paths(root: Path | None = None) -> AppPaths:
    data_root = application_data_dir() if root is None else root
    return AppPaths(
        root=data_root,
        database=data_root / "db",
        market_data=data_root / "market-data",
        models=data_root / "models",
        logs=data_root / "logs",
        debug=data_root / "debug",
        exports=data_root / "exports",
    )


def ensure_app_paths(root: Path | None = None) -> AppPaths:
    paths = app_paths(root)
    for directory in paths.directories:
        directory.mkdir(parents=True, exist_ok=True)
    return paths
