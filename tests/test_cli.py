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


# ---------------------------------------------------------------- users, tokens, audit (M13)
def test_user_and_token_lifecycle_through_the_cli(home):
    r = CliRunner().invoke(app, ["user", "add", "bob", "--role", "member", "--name", "Bob"])
    assert r.exit_code == 0, r.output
    r = CliRunner().invoke(app, ["user", "list"])
    assert "bob" in r.output and "member" in r.output and "owner" in r.output  # owner bootstrapped
    r = CliRunner().invoke(app, ["token", "new", "bob", "--label", "laptop"])
    assert r.exit_code == 0, r.output
    secret = r.output.strip().splitlines()[-1].strip()
    assert len(secret) > 30
    # the secret authenticates, and it is stored only as a hash
    from cadre.accounts import Accounts
    from cadre.store import Store
    accounts = Accounts(Store(Home(home.root).db_path))
    assert accounts.resolve(secret).id == "bob"
    assert secret not in Home(home.root).db_path.read_bytes().decode("utf-8", "replace")
    # revoke by id
    tid = accounts.list_tokens("bob")[0]["id"]
    r = CliRunner().invoke(app, ["token", "revoke", tid])
    assert r.exit_code == 0 and accounts.resolve(secret) is None


def test_cli_refuses_a_bad_role_and_a_duplicate_user(home):
    assert CliRunner().invoke(app, ["user", "add", "bob", "--role", "wizard"]).exit_code == 1
    assert CliRunner().invoke(app, ["user", "add", "bob"]).exit_code == 0
    dup = CliRunner().invoke(app, ["user", "add", "bob"])
    assert dup.exit_code == 1 and "already exists" in dup.output


def test_audit_command_shows_recent_actions(home):
    CliRunner().invoke(app, ["user", "add", "bob"])
    r = CliRunner().invoke(app, ["audit"])
    assert r.exit_code == 0 and "user.created" in r.output and "bob" in r.output


# ---------------------------------------------------------------- teams (M13 phase 2)
def test_team_cli_lifecycle(home):
    R = CliRunner()
    assert R.invoke(app, ["team", "add", "eng", "--name", "Engineering"]).exit_code == 0
    assert R.invoke(app, ["user", "add", "bob"]).exit_code == 0
    assert R.invoke(app, ["team", "member", "add", "eng", "bob"]).exit_code == 0
    r = R.invoke(app, ["team", "budget", "eng", "--runs-per-day", "10", "--concurrent", "2"])
    assert r.exit_code == 0, r.output
    r = R.invoke(app, ["team", "allow", "eng", "groq", "gemini/pro"])
    assert r.exit_code == 0 and "groq" in r.output and "gemini/pro" in r.output
    r = R.invoke(app, ["team", "list"])
    assert "eng" in r.output and "bob" in r.output and "10" in r.output
    from cadre.accounts import Accounts
    from cadre.store import Store
    a = Accounts(Store(Home(home.root).db_path))
    assert a.team("eng")["allow"] == ["gemini/pro", "groq"]
    assert a.team("eng")["budget"]["runs_per_day"] == 10
    # clearing the allowance
    assert R.invoke(app, ["team", "allow", "eng", "--clear"]).exit_code == 0
    assert a.team("eng")["allow"] == []
    # removing a member
    assert R.invoke(app, ["team", "member", "remove", "eng", "bob"]).exit_code == 0
    assert a.team("eng")["members"] == []


def test_team_cli_rejects_bad_input(home):
    R = CliRunner()
    assert R.invoke(app, ["team", "member", "add", "ghost", "bob"]).exit_code == 1  # no team
    assert R.invoke(app, ["team", "add", "eng"]).exit_code == 0
    dup = R.invoke(app, ["team", "add", "eng"])
    assert dup.exit_code == 1 and "already exists" in dup.output
