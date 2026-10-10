"""Where the CourtListener token comes from.

Regression (2026-10-10): the token was exported from a login profile, so agent
tool shells never saw it and every call ran unauthenticated without saying so.
"""
import pytest

from evidence_search.sources import courtlistener as cl


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("COURTLISTENER_TOKEN", raising=False)
    monkeypatch.delenv("COURTLISTENER_TOKEN_FILE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def _write(tmp_path, text):
    d = tmp_path / "courtlistener"
    d.mkdir()
    (d / "token").write_text(text)


def test_no_variable_and_no_file_sends_no_header(clean_env):
    assert cl._headers() == {}


def test_file_is_used_when_the_variable_is_unset(clean_env):
    _write(clean_env, "abc123\n")
    assert cl._headers() == {"Authorization": "Token abc123"}


def test_variable_wins_over_the_file(clean_env, monkeypatch):
    _write(clean_env, "from-file")
    monkeypatch.setenv("COURTLISTENER_TOKEN", "from-env")
    assert cl._headers() == {"Authorization": "Token from-env"}


def test_empty_variable_falls_through_to_the_file(clean_env, monkeypatch):
    _write(clean_env, "from-file")
    monkeypatch.setenv("COURTLISTENER_TOKEN", "  ")
    assert cl._headers() == {"Authorization": "Token from-file"}


def test_empty_file_sends_no_header(clean_env):
    _write(clean_env, "\n")
    assert cl._headers() == {}


def test_explicit_file_path_overrides_the_default(clean_env, monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"
    other.write_text("explicit")
    _write(clean_env, "default")
    monkeypatch.setenv("COURTLISTENER_TOKEN_FILE", str(other))
    assert cl._headers() == {"Authorization": "Token explicit"}
