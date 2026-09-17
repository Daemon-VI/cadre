import re

import httpx

from cadre.clocks import validate
from cadre.config import (
    CadreConfig,
    ModelConfig,
    ProviderConfig,
    apply_diff,
    build_router,
    provider_from_preset,
    refresh_provider,
)
from cadre.presets import CHECKED, PRESETS, is_chat_model
from cadre.router import CallRequest
from cadre.types import Message

from .conftest import MemorySecrets


def test_every_free_preset_model_has_a_source_and_date():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", CHECKED)
    for p in PRESETS.values():
        if p.id == "custom":
            continue
        assert validate(p.day_reset) == p.day_reset, p.id
        assert p.trains_on_free_data in ("yes", "no", "unknown"), p.id
        if p.free and not p.local:
            assert p.policy_source.startswith("https://"), p.id
        for m in p.models:
            assert (m.source or p.source) in ("docs", "reported", "guess"), (p.id, m.name)
            assert m.family, (p.id, m.name)


def test_deepseek_is_paid_and_free_tiers_carry_their_policy():
    assert PRESETS["deepseek"].free is False and "Paid" in PRESETS["deepseek"].note
    assert PRESETS["groq"].trains_on_free_data == "no"
    assert PRESETS["cloudflare"].trains_on_free_data == "no"
    assert PRESETS["gemini"].trains_on_free_data == "yes"
    assert PRESETS["gemini"].day_reset == "America/Los_Angeles"
    assert PRESETS["cloudflare"].day_reset == "UTC"


async def test_gemini_free_models_are_separate_buckets(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-test-not-a-real-key-000")
    pc = provider_from_preset("gemini")
    names = [m.name for m in pc.models]
    assert len(names) >= 7 and len(set(names)) == len(names)
    assert {"gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"} <= set(names)
    assert not any(n.startswith("gemini-2.5") for n in names)  # 404 for new users, 2026-09-17
    assert pc.clock() == "America/Los_Angeles" and pc.trains() == "yes"

    served = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        model = _json.loads(request.content)["model"]
        served.append(model)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    cfg = CadreConfig(providers=[pc])
    router, warnings = build_router(cfg, MemorySecrets(), transport=httpx.MockTransport(handler))
    assert not warnings
    keys = {router.quota(m).key for m in router.models}
    assert len(keys) == len(names)
    # each Flash model allows 5 requests a minute, so a burst spreads across the separate buckets
    # instead of waiting on one model
    for _ in range(19):
        await router.chat(CallRequest([Message(role="user", content="hi")], max_tokens=10))
    await router.aclose()
    counts = {n: served.count(n) for n in set(served)}
    assert served[:5] == ["gemini-3.8-flash"] * 5 and served[5] == "gemini-3.7-flash"
    assert len(counts) >= 4 and max(counts.values()) <= 5
    assert router.quota(router.models[0]).day_requests == 5


async def test_refresh_reports_added_and_removed_and_keeps_overrides():
    pc = provider_from_preset("gemini", models=["gemini-3.8-flash"])
    pc.models[0].rpd = 99
    pc.models[0].source = "owner"
    pc.models.append(ModelConfig(name="gemini-2.0-flash", tier="strong", family="gemini"))

    listing = {"data": [{"id": "models/gemini-3.8-flash"}, {"id": "models/gemini-3.7-flash"},
                        {"id": "models/gemini-embedding-2"}, {"id": "models/gemini-9-secret"}]}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=listing))
    diff = await refresh_provider(pc, MemorySecrets(), transport)
    assert diff.added == ["gemini-3.7-flash"]      # only free chat models the catalogue knows
    assert diff.removed == ["gemini-2.0-flash"]
    assert diff.kept == ["gemini-3.8-flash"]
    apply_diff(pc, diff)
    by = {m.name: m for m in pc.models}
    assert by["gemini-3.8-flash"].rpd == 99 and by["gemini-3.8-flash"].source == "owner"
    assert by["gemini-3.7-flash"].rpd == 20 and by["gemini-3.7-flash"].source == "reported"
    assert by["gemini-2.0-flash"].enabled is False


async def test_refresh_discovers_openrouter_free_models_only():
    pc = ProviderConfig(id="openrouter", preset="openrouter", base_url="https://openrouter.ai/api/v1",
                        key_ref="openrouter")
    listing = {"data": [{"id": "nvidia/nemotron-3-super-120b-a12b:free"}, {"id": "vendor/paid-model"},
                        {"id": "vendor/some-embed:free"}]}
    diff = await refresh_provider(pc, MemorySecrets(), httpx.MockTransport(
        lambda r: httpx.Response(200, json=listing)))
    assert diff.added == ["nvidia/nemotron-3-super-120b-a12b:free"]
    apply_diff(pc, diff)
    m = pc.models[0]
    assert (m.rpm, m.rpd, m.tier, m.family, m.source) == (20, 50, "strong", "nemotron", "docs")


async def test_a_provider_that_cannot_list_is_reported_not_fatal():
    pc = provider_from_preset("groq")
    diff = await refresh_provider(pc, MemorySecrets(), httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})))
    assert diff.error and not diff.added and not diff.removed


def test_chat_model_filter():
    assert is_chat_model("gemini-3.8-flash") and is_chat_model("qwen/qwen3.8-27b")
    for name in ("gemini-embedding-2", "gemini-2.5-flash-preview-tts", "whisper-large-v3",
                 "gemini-3.8-live", "meta-llama/llama-prompt-guard-2-22m"):
        assert not is_chat_model(name), name


def test_old_config_without_new_fields_uses_preset_values():
    pc = ProviderConfig.model_validate({"id": "g", "preset": "gemini", "base_url": "https://x",
                                        "models": [{"name": "gemini-2.5-flash", "rpd": 250}]})
    assert pc.clock() == "America/Los_Angeles" and pc.trains() == "yes" and pc.reserve_pct == 10
    assert pc.models[0].limits(pc.reserve_pct).rpd == 225
    local = ProviderConfig(id="o", preset="ollama", base_url="http://127.0.0.1:11434/v1", local=True)
    assert local.trains() == "no"
