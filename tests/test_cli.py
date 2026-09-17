from typer.testing import CliRunner

from cadre.cli import app
from cadre.config import Home


def test_provider_add_without_a_key_fails_fast_instead_of_hanging(home):
    result = CliRunner().invoke(app, ["provider", "add", "groq", "--no-test"], input="")
    assert result.exit_code == 1
    assert "no key found for groq" in result.output and "cadre/groq" in result.output
    assert Home(home.root).load_config().providers == []


def test_provider_add_uses_a_key_from_the_environment(home, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-not-real-0000000")
    result = CliRunner().invoke(app, ["provider", "add", "groq", "--no-test"])
    assert result.exit_code == 0, result.output
    assert "env:GROQ_API_KEY" in result.output and "gsk-test" not in result.output
    assert [p.id for p in Home(home.root).load_config().providers] == ["groq"]
