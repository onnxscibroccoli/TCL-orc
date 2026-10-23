from tcl.config import Settings, get_settings


def test_defaults(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in ("TCL_DB", "TCL_TREE_DEPTH", "TCL_CONFIG", "TCL_MODE", "TCL_LOG_LEVEL"):
        monkeypatch.delenv(k, raising=False)
    s = Settings()
    assert s.tree_depth == 20 and s.mode == "fixed" and str(s.db) == "tcl.db"


def test_precedence_yaml_dotenv_env_kwargs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k in ("TCL_DB", "TCL_TREE_DEPTH", "TCL_CONFIG", "TCL_MODE"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "config.yaml").write_text("tree_depth: 8\nmode: linear\ndb: y.db\n")
    assert Settings().tree_depth == 8 and Settings().mode == "linear"
    (tmp_path / ".env").write_text("TCL_TREE_DEPTH=10\n")
    assert Settings().tree_depth == 10 and Settings().mode == "linear"
    monkeypatch.setenv("TCL_TREE_DEPTH", "12")
    assert Settings().tree_depth == 12
    assert get_settings(tree_depth=14, db=None).tree_depth == 14


def test_validation(monkeypatch, tmp_path):
    import pytest
    from pydantic import ValidationError
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TCL_TREE_DEPTH", "99")
    with pytest.raises(ValidationError):
        Settings()
