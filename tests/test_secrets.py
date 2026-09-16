import httpx

from cadre.config import CadreConfig, build_router, provider_from_preset
from cadre.runs import RunManager
from cadre.secrets import REDACTOR, SecretStore, env_name

from .conftest import MemorySecrets


def test_lookup_order_env_then_preset_variable(monkeypatch):
    store = SecretStore()
    assert store.where("groq", "GROQ_API_KEY") is None
    monkeypatch.setenv("GROQ_API_KEY", "from-preset-var-123")
    assert store.where("groq", "GROQ_API_KEY") == "env:GROQ_API_KEY"
    monkeypatch.setenv(env_name("groq"), "from-cadre-var-456")
    assert store.get("groq", "GROQ_API_KEY") == "from-cadre-var-456"
    assert "from-cadre-var-456" in REDACTOR.known()


def test_keyring_disabled_gives_a_useful_error():
    try:
        SecretStore().set("groq", "value-123456789")
    except RuntimeError as e:
        assert "CADRE_KEY_GROQ" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_provider_without_a_key_is_skipped_with_a_warning():
    cfg = CadreConfig(providers=[provider_from_preset("groq")])
    router, warnings = build_router(cfg, SecretStore())
    assert router.usable() == [] and "no key found" in warnings[0]


async def test_a_leaked_key_never_reaches_the_database(home, monkeypatch):
    key = "gsk_planted_leak_check_9876543210"
    echo = httpx.MockTransport(lambda req: httpx.Response(
        401, json={"error": {"message": f"Invalid API Key: {req.headers['authorization']}"}}))
    secrets = MemorySecrets()
    secrets.set("groq", key)
    cfg = home.load_config()
    cfg.providers = [provider_from_preset("groq")]
    home.save_config(cfg)
    m = RunManager(home, secrets=secrets, transport=echo)
    rid = m.create("decision-board", f"is {key} a good key?")
    run = await m.execute(rid)
    assert run["status"] == "failed" and "key rejected" in run["error"]
    m.store.close()
    blob = b"".join(p.read_bytes() for p in home.root.glob("cadre.sqlite*"))
    assert key.encode() not in blob
    assert b"***" in blob
