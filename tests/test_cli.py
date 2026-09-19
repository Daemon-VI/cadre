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


def test_provider_add_with_a_stored_key_says_how_to_replace_it(home, monkeypatch):
    # rotating a key and re-running `provider add` kept the old key without a word
    from cadre.secrets import SecretStore

    monkeypatch.setattr(SecretStore, "where", lambda self, ref, env_hint=None: "keyring")
    result = CliRunner().invoke(app, ["provider", "add", "groq", "--no-test"])
    assert result.exit_code == 0, result.output
    assert "To replace it, run `cadre provider key groq`" in " ".join(result.output.split())


def test_provider_add_with_an_env_key_names_the_variable_to_change(home, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-not-real-0000000")
    result = CliRunner().invoke(app, ["provider", "add", "groq", "--no-test"])
    assert "To replace it, change GROQ_API_KEY" in " ".join(result.output.split())
