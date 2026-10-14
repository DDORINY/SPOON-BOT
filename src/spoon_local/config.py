"""Explicit project-root dotenv loading without changing process environment."""

import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_path: Path


def load_settings(
    project_root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """OS values override .env; relative data paths are rooted in the project."""
    root = project_root.resolve()
    file_values = dotenv_values(root / ".env", interpolate=False)
    environment = os.environ if environ is None else environ
    raw = environment.get("SPOON_LOCAL_DATA_DIR", file_values.get("SPOON_LOCAL_DATA_DIR"))
    directory = Path(raw).expanduser() if raw else Path("data")
    if not directory.is_absolute():
        directory = root / directory
    return Settings(database_path=directory.resolve() / "spoon_local.sqlite3")


def get_database_path() -> Path:
    return load_settings().database_path
