"""SQLite initialization using the Python standard library."""

import sqlite3
from pathlib import Path


def initialize_database(path: Path) -> None:
    """Create the data directory and an idempotent schema version table."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY CHECK (version = 1))"
            )
            connection.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (1)")
    finally:
        connection.close()
