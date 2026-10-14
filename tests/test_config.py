from pathlib import Path

from spoon_local.config import load_settings


def test_dotenv_loading(tmp_path):
    (tmp_path / ".env").write_text("SPOON_LOCAL_DATA_DIR=local-records\n")
    settings = load_settings(tmp_path, environ={})
    assert settings.database_path == tmp_path / "local-records" / "spoon_local.sqlite3"
    assert not settings.database_path.exists()


def test_os_environment_wins(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SPOON_LOCAL_DATA_DIR=from-file\n")
    monkeypatch.setenv("SPOON_LOCAL_DATA_DIR", "from-os")
    assert load_settings(tmp_path).database_path == tmp_path / "from-os" / "spoon_local.sqlite3"
    assert __import__("os").environ["SPOON_LOCAL_DATA_DIR"] == "from-os"


def test_missing_dotenv_default_is_independent_of_cwd(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert load_settings(root, environ={}).database_path == root / "data" / "spoon_local.sqlite3"


def test_absolute_data_directory(tmp_path):
    absolute = tmp_path / "absolute"
    assert load_settings(tmp_path, environ={"SPOON_LOCAL_DATA_DIR": str(absolute)}).database_path == absolute / "spoon_local.sqlite3"


def test_empty_os_value_overrides_dotenv_with_default(tmp_path):
    (tmp_path / ".env").write_text("SPOON_LOCAL_DATA_DIR=from-file\n")
    assert load_settings(tmp_path, environ={"SPOON_LOCAL_DATA_DIR": ""}).database_path == tmp_path / "data" / "spoon_local.sqlite3"


def test_project_root_is_derived_from_source():
    from spoon_local.config import PROJECT_ROOT

    assert PROJECT_ROOT == Path(__file__).resolve().parents[1]
