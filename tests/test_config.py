import os

import pytest

from triage_pipeline.config import ConfigError, load_config_file


def _write(tmp_path, text, mode=0o600):
    f = tmp_path / "config.env"
    f.write_text(text)
    f.chmod(mode)
    return str(f)


def test_loads_values_and_formats(tmp_path, monkeypatch):
    for k in ("COPILOT_GITHUB_TOKEN", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    path = _write(tmp_path, '# c\n\nexport COPILOT_GITHUB_TOKEN="abc"\nJIRA_EMAIL=a@b.c\nJIRA_API_TOKEN=\'t=1\'\n')
    loaded = load_config_file(path)
    assert sorted(loaded) == ["COPILOT_GITHUB_TOKEN", "JIRA_API_TOKEN", "JIRA_EMAIL"]
    assert os.environ["COPILOT_GITHUB_TOKEN"] == "abc"
    assert os.environ["JIRA_API_TOKEN"] == "t=1"
    for k in loaded:
        monkeypatch.delenv(k)


def test_exported_variable_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "from-env")
    load_config_file(_write(tmp_path, "COPILOT_GITHUB_TOKEN=from-file\n"))
    assert os.environ["COPILOT_GITHUB_TOKEN"] == "from-env"


def test_disallowed_variable_ignored(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PATH", "/usr/bin")
    load_config_file(_write(tmp_path, "PATH=/evil\n"))
    assert os.environ["PATH"] == "/usr/bin"
    assert "Ignoring" in capsys.readouterr().err


def test_missing_explicit_file_errors_but_default_is_silent(tmp_path, monkeypatch):
    with pytest.raises(ConfigError):
        load_config_file(str(tmp_path / "nope.env"))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert load_config_file() == []


def test_warns_on_loose_permissions(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    load_config_file(_write(tmp_path, "JIRA_EMAIL=x\n", mode=0o644))
    assert "chmod 600" in capsys.readouterr().err
    monkeypatch.delenv("JIRA_EMAIL")
