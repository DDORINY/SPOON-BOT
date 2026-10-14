"""Local configuration; environment variables are read at app creation."""

import os
from pathlib import Path


def get_database_path() -> Path:
    """Resolve data relative to the current working directory by default."""
    return Path(os.environ.get("SPOON_LOCAL_DATA_DIR") or "data") / "spoon_local.sqlite3"
