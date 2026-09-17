import pytest

from cadre.providers import AuthFailed, RateLimited, RequestTooLarge, ScriptedProvider, ToolsUnsupported
from cadre.quota import Limits, QuotaBook
from cadre.router import CallRequest, ModelEntry, NoModelAvailable, QuotaParked, Router
from cadre.types import Message, ToolSpec

MSG = [Message(role="user", content="hello")]
TOOL = ToolSpec(name="t", description="d", parameters={"type": "object", "properties": {}})


def entries(*specs):
    return [ModelEntry(p, n, tier=t, family=f, priority=i * 10, limits=Limits())
            for i, (p, n, t, f) in enumerate(specs)]


async def test_rate_limit_falls_back_and_cools_the_model():
    a = ScriptedProvider("a", [RateLimited("slow down", retry_after=30)])
    b = ScriptedProvider("b", ["from b"])
    events = []
    r = Router({"a": a, "b": b}, entries(("a", "m", "strong", "x"), ("b", "m", "strong", "y")))
    res = await r.chat(CallRequest(MSG), emit=lambda k, d: events.append(k))
    assert res.response.content == "from b" and res.entry.provider == "b"
    assert "route.fallback" in events
    assert r.quota(r.models[0]).wait_time(10)[0] == pytest.approx(30, abs=1)


async def test_rejected_key_disables_the_provider_for_the_session():
    a = ScriptedProvider("a", [AuthFailed("401 bad key")])
    b = ScriptedProvider("b", ["ok", "ok again"])
    r = Router({"a": a, "b": b}, entries(("a", "m", "strong", "x"), ("b", "m", "strong", "y")))
    await r.chat(CallRequest(MSG))
    assert "a" in r.disabled
    await r.chat(CallRequest(MSG))
    assert len(a.calls) == 1


async def test_reviewer_is_routed_to_a_different_family():
    a = ScriptedProvider("a", lambda *_: "a")
    b = ScriptedProvider("b", lambda *_: "b")
    r = Router({"a": a, "b": b}, entries(("a", "m", "strong", "gpt-oss"), ("b", "m", "strong", "gemini")))
    res = await r.chat(CallRequest(MSG, avoid_families=("gpt-oss",)))
    assert res.entry.family == "gemini" and res.independent is True


async def test_independence_prefers_another_family_over_tier():
    a = ScriptedProvider("a", lambda *_: "a")
    r = Router({"a": a}, entries(("a", "big", "strong", "gpt-oss"), ("a", "small", "fast", "llama")))
    res = await r.chat(CallRequest(MSG, tier="strong", avoid_families=("gpt-oss",)))
    assert res.entry.name == "small" and res.independent is True


async def test_single_family_proceeds_but_records_it_is_not_independent():
    a = ScriptedProvider("a", lambda *_: "a")
    r = Router({"a": a}, entries(("a", "m", "strong", "gpt-oss")))
    res = await r.chat(CallRequest(MSG, avoid_families=("gpt-oss",)))
    assert res.independent is False


async def test_tools_unsupported_switches_to_json_protocol_and_retries():
    a = ScriptedProvider("a", [ToolsUnsupported("no tools"), '{"answer": "fine"}'])
    r = Router({"a": a}, entries(("a", "m", "strong", "x")))
    res = await r.chat(CallRequest(MSG, tools=[TOOL]))
    assert res.response.content == "fine"
    assert [c["protocol"] for c in a.calls] == ["native", "json"]


async def test_too_large_request_excludes_that_model_only():
    a = ScriptedProvider("a", [RequestTooLarge("413")])
    b = ScriptedProvider("b", ["b"])
    r = Router({"a": a, "b": b}, entries(("a", "m", "strong", "x"), ("b", "m", "strong", "y")))
    assert (await r.chat(CallRequest(MSG))).entry.provider == "b"


async def test_waits_for_the_only_model_when_it_frees_soon():
    now = [0.0]
    slept = []

    async def sleep(s):
        slept.append(s)
        now[0] += s

    a = ScriptedProvider("a", lambda *_: "ok")
    models = [ModelEntry("a", "m", tier="strong", family="x", limits=Limits(rpm=1))]
    r = Router({"a": a}, models, QuotaBook(clock=lambda: now[0]), sleep=sleep, max_wait=90)
    await r.chat(CallRequest(MSG))
    now[0] += 20
    await r.chat(CallRequest(MSG))
    assert slept and slept[0] == pytest.approx(40, abs=0.1)


async def test_exhausted_quota_parks_with_a_reason_that_names_the_model():
    # v0.1 failed here with NoModelAvailable; since ADR-017 a daily block parks the run instead
    a = ScriptedProvider("a", lambda *_: "ok")
    models = [ModelEntry("a", "m", tier="strong", family="x", limits=Limits(rpd=1))]
    r = Router({"a": a}, models)
    await r.chat(CallRequest(MSG))
    with pytest.raises(QuotaParked) as info:
        await r.chat(CallRequest(MSG))
    assert "a/m" in str(info.value) and "daily limit" in str(info.value)
    assert info.value.blocks[0]["model"] == "a/m" and info.value.resume_in > 0


async def test_a_long_minute_block_still_fails_instead_of_parking():
    a = ScriptedProvider("a", lambda *_: "ok")
    models = [ModelEntry("a", "m", tier="strong", family="x", limits=Limits(rpm=1))]
    r = Router({"a": a}, models, max_wait=10)  # a 60 s minute window is longer than max_wait
    await r.chat(CallRequest(MSG))
    with pytest.raises(NoModelAvailable, match="requests/min"):
        await r.chat(CallRequest(MSG))


async def test_no_downgrade_when_the_agent_forbids_it():
    a = ScriptedProvider("a", lambda *_: "ok")
    r = Router({"a": a}, entries(("a", "small", "fast", "x")))
    with pytest.raises(NoModelAvailable):
        await r.chat(CallRequest(MSG, tier="strong", allow_downgrade=False))
    assert (await r.chat(CallRequest(MSG, tier="strong"))).entry.name == "small"


async def test_busy_model_rests_longer_each_time_and_recovers():
    from cadre.providers import Unavailable

    now = [0.0]
    a = ScriptedProvider("a", [Unavailable("503 high demand"), Unavailable("503 high demand"), "ok"])
    b = ScriptedProvider("b", lambda *_: "b")
    models = entries(("a", "m", "strong", "x"), ("b", "m", "strong", "y"))
    r = Router({"a": a, "b": b}, models, QuotaBook(clock=lambda: now[0]))
    await r.chat(CallRequest(MSG))  # a fails once, b answers
    assert r.quota(models[0]).wait_time(10)[0] == pytest.approx(30)
    now[0] += 31
    await r.chat(CallRequest(MSG))  # a fails again: now it rests 60 s
    assert r.quota(models[0]).wait_time(10)[0] == pytest.approx(60)
    now[0] += 61
    assert (await r.chat(CallRequest(MSG))).entry.provider == "a"
    assert "a/m" not in r._unavailable


async def test_a_model_that_answers_404_is_skipped_for_the_session():
    from cadre.providers import ModelGone

    a = ScriptedProvider("a", [ModelGone("404 no longer available to new users")])
    b = ScriptedProvider("b", lambda *_: "b")
    r = Router({"a": a, "b": b}, entries(("a", "old", "strong", "x"), ("b", "m", "strong", "y")))
    assert (await r.chat(CallRequest(MSG))).entry.provider == "b"
    assert (await r.chat(CallRequest(MSG))).entry.provider == "b"
    assert len(a.calls) == 1 and "a/old" in r.gone


async def test_a_tool_loop_stays_on_its_model():
    a = ScriptedProvider("a", lambda *_: "a")
    b = ScriptedProvider("b", lambda *_: "b")
    r = Router({"a": a, "b": b}, entries(("a", "m", "strong", "x"), ("b", "m", "strong", "x")))
    assert (await r.chat(CallRequest(MSG))).entry.provider == "a"  # priority
    assert (await r.chat(CallRequest(MSG, prefer="b/m"))).entry.provider == "b"


async def test_long_contention_waits_instead_of_failing():
    # 2026-09-17: four QA reviews shared one independent model; one call kept losing the freed slot
    now = [0.0]
    a = ScriptedProvider("a", lambda *_: "ok")
    models = [ModelEntry("a", "m", tier="strong", family="x", limits=Limits(rpm=1))]
    r = Router({"a": a}, models, QuotaBook(clock=lambda: now[0]), max_wait=90)
    q = r.quota(models[0])
    steals = [5]

    async def sleep(s):
        now[0] += s
        if steals[0]:  # another task takes the slot that just freed up
            steals[0] -= 1
            q.settle(q.reserve(10), 10)

    r._sleep = sleep
    await r.chat(CallRequest(MSG))
    res = await r.chat(CallRequest(MSG))
    assert res.waited > 300  # six waits of ~60 s: more than v0.1's 270 s cap
    assert len(a.calls) == 2


async def test_a_daily_quota_429_blocks_the_model_until_its_reset():
    daily = RateLimited("429 quota", retry_after=41, daily=True, limit=6)
    a = ScriptedProvider("a", [daily])
    b = ScriptedProvider("b", lambda *_: "b")
    models = [ModelEntry("a", "m", tier="strong", family="x", priority=1, limits=Limits(rpd=20)),
              ModelEntry("b", "m", tier="strong", family="y", priority=2, limits=Limits())]
    r = Router({"a": a, "b": b}, models)
    assert (await r.chat(CallRequest(MSG))).entry.provider == "b"
    block = r.quota(models[0]).block(10)
    assert block.kind == "daily" and block.wait > 60 and models[0].limits.rpd == 6
    assert (await r.chat(CallRequest(MSG))).entry.provider == "b" and len(a.calls) == 1
