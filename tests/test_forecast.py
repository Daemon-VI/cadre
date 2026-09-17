from datetime import UTC, datetime

from cadre.config import CadreConfig, build_router, provider_from_preset
from cadre.forecast import Estimate, estimate, forecast, percentile, usage_ledger
from cadre.org import find_org_text, load_org_text
from cadre.providers import ScriptedProvider
from cadre.quota import Limits, QuotaBook
from cadre.router import ModelEntry, Router

from .conftest import MemorySecrets


def ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def test_usage_ledger_by_day_provider_model(store):
    now = ts("2026-09-17T12:00:00")
    cfg = CadreConfig(providers=[provider_from_preset("groq"), provider_from_preset("gemini")])
    store.quota_save("groq/openai/gpt-oss-120b", "2026-09-17T09@rolling", 100, 40_000)
    store.quota_save("groq/openai/gpt-oss-120b", "2026-09-17T10@rolling", 50, 10_000)
    store.quota_save("gemini/gemini-3.8-flash", "2026-09-17@America/Los_Angeles", 15, 30_000)
    store.quota_save("groq/openai/gpt-oss-120b", "2026-09-10T09@rolling", 7, 70)  # outside the window
    rows = usage_ledger(store, cfg, days=2, now=now)
    by = {(r["day"], r["provider"], r["model"]): r for r in rows}
    assert set(by) == {("2026-09-17", "groq", "openai/gpt-oss-120b"),
                       ("2026-09-17", "gemini", "gemini-3.8-flash")}
    groq = by[("2026-09-17", "groq", "openai/gpt-oss-120b")]
    assert (groq["requests"], groq["tokens"], groq["rpd"]) == (150, 50_000, 1000)
    assert groq["share"] == 0.25  # tokens: 50k of 200k beats requests: 150 of 1000
    gem = by[("2026-09-17", "gemini", "gemini-3.8-flash")]
    assert gem["share"] == 0.75 and gem["day_reset"] == "America/Los_Angeles"
    assert gem["next_reset"] == "18 Sep 12:30 IST"


def test_percentile_is_nearest_rank():
    assert percentile([10, 12, 14, 16, 40], 50) == 14
    assert percentile([10, 12, 14, 16, 40], 90) == 40
    assert percentile([], 90) == 0


def test_no_history_is_labelled_estimated(store):
    org = load_org_text(find_org_text("software-team")[1])
    est = estimate(store, org, "a CSV to Markdown converter")
    assert est.basis == "no history, estimated from template size" and est.n == 0
    assert 5 < est.calls <= est.calls_p90 and 0 < est.tokens <= est.tokens_p90
    assert est.largest_call > 3000  # engineer: prompt + 3,000 reserved output tokens
    assert any("review loop engineer" in line for line in est.per_step)


def test_measured_history_uses_median_and_p90(store):
    org = load_org_text("name: board\nagents: [{id: a, role: r}]\nworkflow: {agent: a, task: go}\n")
    for i, calls in enumerate([10, 12, 14, 16, 40]):
        rid = store.create_run("board", "", "g", {})
        store.update_run(rid, status="succeeded" if i != 2 else "unapproved", active_seconds=60 * (i + 1))
        for _ in range(calls):
            store.add_usage(rid, "a", "groq", "m", 900, 100)
    demo = store.create_run("board", "", "g", {"demo": True})
    store.update_run(demo, status="succeeded")
    store.add_usage(demo, "a", "demo-a", "x", 1, 1)
    failed = store.create_run("board", "", "g", {})
    store.update_run(failed, status="failed")
    store.add_usage(failed, "a", "groq", "m", 1, 1)

    est = estimate(store, org, "g")
    assert est.basis == "measured, n = 5" and est.n == 5
    assert (est.calls, est.calls_p90) == (14, 40)
    assert (est.tokens, est.tokens_p90) == (14_000, 40_000)
    assert est.minutes == 3.0


def router_with(*models: ModelEntry) -> Router:
    p = ScriptedProvider("p", lambda *_: "ok")
    return Router({"p": p}, list(models), QuotaBook())


def est(calls: float, tokens: float, largest: int = 2000) -> Estimate:
    return Estimate(calls, calls, tokens, tokens, None, 0, largest, "no history, estimated from template size")


def test_verdicts_fit_now_wait_days_cannot():
    groq = [ModelEntry("p", f"m{i}", tier="strong", family="f", trains="no",
                       limits=Limits(rpm=30, rpd=1000, tpm=8000, tpd=200_000)) for i in range(3)]
    now = forecast(est(5, 5000), router_with(*groq))
    assert now.verdict == "fits_now" and now.headline == "fits now"

    wait = forecast(est(20, 40_000), router_with(groq[0]))
    assert wait.verdict == "fits_today" and wait.wait_minutes == 5
    assert wait.headline == "fits today after ~5 min of waits"

    flash = ModelEntry("p", "flash", tier="strong", family="g", limits=Limits(rpm=5, rpd=20, tpm=250_000))
    days = forecast(est(50, 50_000), router_with(flash))
    assert days.verdict == "needs_days" and days.days == 3
    assert "20 of ~50 calls" in days.reasons[0]

    big = forecast(est(5, 5000, largest=9000), router_with(*groq))
    assert big.verdict == "cannot_run" and "tokens-per-minute" in big.headline

    assert forecast(est(1, 100), router_with()).verdict == "cannot_run"
    private = forecast(est(1, 100), router_with(flash), private=True)
    assert private.verdict == "cannot_run" and "private" in private.headline
    lines = "\n".join(now.lines())
    assert "basis: no history, estimated from template size" in lines


def test_used_quota_counts_against_today():
    flash = ModelEntry("p", "flash", tier="strong", family="g", limits=Limits(rpd=20))
    r = router_with(flash)
    q = r.quota(flash)
    for _ in range(18):
        q.settle(q.reserve(10), 10)
    f = forecast(est(5, 500), r)
    assert f.requests_left == 2 and f.verdict == "needs_days"


def test_reserve_pct_shrinks_daily_caps(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test-not-real-000000")
    pc = provider_from_preset("groq")
    pc.reserve_pct = 25
    router, _ = build_router(CadreConfig(providers=[pc]), MemorySecrets())
    assert router.models[0].limits.rpd == 750 and router.models[0].limits.rpm == 30
    pc.reserve_pct = 0
    router, _ = build_router(CadreConfig(providers=[pc]), MemorySecrets())
    assert router.models[0].limits.rpd == 1000
