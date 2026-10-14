import sqlite3

from spoon_local.database import initialize_database


def test_database_initialization_is_idempotent(tmp_path):
    path = tmp_path / "nested" / "local.sqlite3"
    initialize_database(path)
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version FROM schema_version").fetchall() == [(1,)]
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.execute("INSERT INTO probe VALUES (?)", ("local",))
        connection.commit()
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM probe").fetchone() == ("local",)
