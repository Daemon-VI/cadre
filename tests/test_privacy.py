from cadre.engine import RunOptions
from cadre.providers import ScriptedProvider
from cadre.quota import Limits, QuotaBook
from cadre.router import ModelEntry, Router
from cadre.runs import RunManager

ORG = "name: t\nagents: [{id: a, role: r}]\nworkflow: {agent: a, task: go}\n"


def mixed_router():
    trains = ScriptedProvider("trains", lambda *_: "from the training provider")
    safe = ScriptedProvider("safe", lambda *_: "from the safe provider")
    models = [
        ModelEntry("trains", "big", tier="strong", family="x", priority=1, limits=Limits(), trains="yes"),
        ModelEntry("trains", "other", tier="strong", family="y", priority=2, limits=Limits(), trains="unknown"),
        ModelEntry("safe", "small", tier="fast", family="z", priority=50, limits=Limits(), trains="no"),
    ]
    return Router({"trains": trains, "safe": safe}, models, QuotaBook()), trains, safe


async def test_private_run_never_calls_a_training_provider(home):
    router, trains, safe = mixed_router()
    m = RunManager(home, router=router)
    rid = m.create("", "g", RunOptions(privacy="private"), org_yaml=ORG)
    run = await m.execute(rid)
    assert run["status"] == "succeeded" and run["result"] == "from the safe provider"
    assert trains.calls == [] and len(safe.calls) == 1
    assert m.store.get_run(rid)["privacy"] == "private"


async def test_excluded_models_are_recorded(home):
    router, *_ = mixed_router()
    m = RunManager(home, router=router)
    rid = m.create("", "g", org_yaml="privacy: private\n" + ORG)  # the org itself asks for privacy
    await m.execute(rid)
    ev = m.store.events(rid, kinds=("privacy.excluded",))[0]["data"]["models"]
    assert {e["model"]: e["trains_on_free_data"] for e in ev} == {"trains/big": "yes", "trains/other": "unknown"}


async def test_private_run_with_nothing_left_fails_before_any_call(home):
    trains = ScriptedProvider("trains", lambda *_: "should never run")
    router = Router({"trains": trains}, [ModelEntry("trains", "big", tier="strong", family="x",
                                                    limits=Limits(), trains="unknown")], QuotaBook())
    m = RunManager(home, router=router)
    rid = m.create("", "g", RunOptions(privacy="private"), org_yaml=ORG)
    run = await m.execute(rid)
    assert run["status"] == "failed" and "private run" in run["error"] and "trains" in run["error"]
    assert trains.calls == []
    assert m.store.usage_totals(rid)["calls"] == 0


async def test_standard_run_uses_the_preferred_model(home):
    router, trains, safe = mixed_router()
    m = RunManager(home, router=router)
    rid = m.create("", "g", org_yaml=ORG)
    run = await m.execute(rid)
    assert run["result"] == "from the training provider" and safe.calls == []
