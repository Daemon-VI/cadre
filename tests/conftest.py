from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pytest

from cadre.config import Home
from cadre.engine import Engine, RunContext, RunOptions
from cadre.org import load_org_text
from cadre.providers import ScriptedProvider
from cadre.quota import Limits, QuotaBook
from cadre.router import ModelEntry, Router
from cadre.secrets import SecretStore
from cadre.store import Store


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CADRE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CADRE_NO_KEYRING", "1")
    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def home(tmp_path: Path) -> Home:
    return Home(tmp_path / "home").ensure()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "t.sqlite")
    yield s
    s.close()


class MemorySecrets(SecretStore):
    """Keyring stand-in for tests."""

    def __init__(self):
        self.data: dict[str, str] = {}

    def _lookup(self, ref, env_hint):
        if ref in self.data:
            return "keyring", self.data[ref]
        return super()._lookup(ref, env_hint)

    def set(self, ref, value):
        self.data[ref] = value.strip()
        return "keyring"

    def delete(self, ref):
        return self.data.pop(ref, None) is not None


_AGENT = re.compile(r'\(id "([a-z0-9_-]+)"\)')


def agent_of(messages) -> str:
    m = _AGENT.search(messages[0].content)
    return m.group(1) if m else "?"


def by_agent(replies: dict[str, list]):
    """A script that answers each agent from its own queue of replies."""
    queues = defaultdict(list, {k: list(v) for k, v in replies.items()})

    def script(model, messages, tools):
        who = agent_of(messages)
        if not queues[who]:
            raise AssertionError(f"no scripted reply left for {who}")
        reply = queues[who].pop(0)
        return reply(model, messages, tools) if callable(reply) else reply

    script.queues = queues
    return script


def two_family_router(script_a, script_b=None, **kw) -> tuple[Router, ScriptedProvider, ScriptedProvider]:
    a = ScriptedProvider("pa", script_a)
    b = ScriptedProvider("pb", script_b or script_a)
    models = [
        ModelEntry("pa", "big-a", tier="strong", family="fam-a", priority=10, limits=Limits()),
        ModelEntry("pb", "big-b", tier="strong", family="fam-b", priority=20, limits=Limits()),
    ]
    return Router({"pa": a, "pb": b}, models, QuotaBook(), **kw), a, b


def make_engine(tmp_path: Path, store: Store, org_yaml: str, router: Router, goal: str = "the goal",
                options: RunOptions | None = None, run_id: str | None = None,
                approver=None) -> tuple[Engine, RunContext]:
    org = load_org_text(org_yaml)
    rid = run_id or store.create_run(org.name, org_yaml, goal, {})
    ctx = RunContext(rid, org, goal, store, router, tmp_path / "runs" / rid,
                     options or RunOptions(), approver, poll=0.01)
    return Engine(ctx), ctx
