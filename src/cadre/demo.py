"""The offline demo provider (FR-7.4, ADR-015).

Two fake endpoints from two fake model families, so independence routing has somewhere to go.
The "brain" reads the structure of the prompt — which tools are offered, which JSON shape is
asked for, which files the task names — and does the plumbing-correct thing. It is not
intelligent, and every answer it writes says `[demo]` so nobody mistakes it for a model.
"""

from __future__ import annotations

import json
import re
import zlib

from .providers import ScriptedProvider
from .quota import Limits
from .router import ModelEntry
from .types import ChatResponse, Message, ToolCall, ToolSpec

_FILE = re.compile(r"`?\b([\w./-]+\.(?:py|md|txt|json|csv|html))\b`?")
_ID = re.compile(r'\(id "([a-z0-9_-]+)"\)')
_ROLE = re.compile(r"^You are (.+?) \(id ")


def _stable(text: str) -> int:
    return zlib.crc32(text.encode("utf-8"))


def _py_module(name: str) -> str:
    return (f'"""{name} — written by the Cadre demo provider."""\n\n\n'
            "def add(a: int, b: int) -> int:\n    return a + b\n")


def _py_test(target: str) -> str:
    mod = target[:-3].replace("/", ".")
    return (f"import unittest\n\nfrom {mod} import add\n\n\n"
            "class DemoTest(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n\n"
            'if __name__ == "__main__":\n    unittest.main()\n')


def _content(path: str, task: str, others: list[str]) -> str:
    base = path.rsplit("/", 1)[-1]
    if base.startswith("test_") and base.endswith(".py"):
        target = next((o for o in others if o.endswith(".py") and not
                       o.rsplit("/", 1)[-1].startswith("test_")), "app.py")
        return _py_test(target)
    if base.endswith(".py"):
        return _py_module(base)
    if base.endswith(".json"):
        return json.dumps({"demo": True, "task": task[:200]}, indent=2)
    return f"# {base}\n\n[demo] Placeholder written for the task:\n\n> {task[:400]}\n"


def brain(label: str):
    def reply(model: str, messages: list[Message], tools: list[ToolSpec]) -> ChatResponse | str:
        system = messages[0].content if messages else ""
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        first_user = next((m.content for m in messages if m.role == "user"), "")
        me = (_ID.search(system) or [None, "agent"])[1]
        role = (_ROLE.search(system) or [None, "agent"])[1]
        names = {t.name for t in tools}
        written = [str(tc.arguments.get("path")) for m in messages if m.role == "assistant"
                   for tc in m.tool_calls if tc.name == "write_file"]
        checked = any(tc.name == "run_check" for m in messages for tc in m.tool_calls)
        wants_json = ("ONLY a JSON object" in system or "ONLY the JSON object" in user)

        if wants_json:
            schema = system + user
            if '"approve"' in schema:
                return json.dumps({"approve": True, "summary": f"[demo:{label}] {me} found it complete.",
                                   "issues": []})
            if '"tasks"' in schema:
                workers = re.findall(r"^- ([a-z][a-z0-9_-]*): ", first_user.split(
                    "WORKERS YOU CAN ASSIGN:", 1)[-1], re.M)
                workers = workers or ["worker"]
                tasks = []
                for i, w in enumerate(workers[:3]):
                    tasks.append({"id": f"t{i + 1}", "title": f"Deliver the {w} part",
                                  "assignee": w, "depends_on": [f"t{i}"] if i else [],
                                  "details": f"Write {w}_output.md with your contribution."})
                return json.dumps({"tasks": tasks})
            if '"options": [' in schema and '"title"' in schema:
                recs = re.findall(r"recommends: (.+)", first_user)
                titles = list(dict.fromkeys(r.strip() for r in recs if r.strip()))[:4]
                while len(titles) < 2:
                    titles.append(["Proceed now", "Run a small pilot first"][len(titles)])
                return json.dumps({"options": [{"title": t, "summary": f"[demo] {t}."} for t in titles]})
            if '"choice"' in schema:
                ids = re.findall(r"^([A-H])\. ", first_user, re.M) or ["A"]
                pick = ids[_stable(me) % len(ids)]
                return json.dumps({"choice": pick, "confidence": 0.6,
                                   "reason": f"[demo:{label}] {me} prefers {pick}."})
            if '"position"' in schema:
                opts = ["Proceed now", "Run a small pilot first", "Do not proceed"]
                rec = opts[_stable(me) % 2]
                return json.dumps({"position": f"[demo:{label}] As {role}, I lean towards: {rec}.",
                                   "options": opts, "recommendation": rec,
                                   "reasoning": "[demo] Deterministic placeholder reasoning."})
            return json.dumps({"answer": "[demo] no JSON shape recognised"})

        if "write_file" in names:
            task = first_user.split("YOUR TASK:", 1)[-1].split("WORKSPACE FILES:", 1)[0]
            wanted = list(dict.fromkeys(_FILE.findall(task))) or [f"{me}_output.md"]
            listing = first_user.split("WORKSPACE FILES:", 1)[-1]
            existing = set(re.findall(r"^(\S+) \(\d+ B\)$", listing, re.M))
            todo = [f for f in wanted if f not in written and f not in existing]
            if todo:
                path = todo[0]
                return ChatResponse(tool_calls=[ToolCall(
                    id=f"call_{len(written) + 1}", name="write_file",
                    arguments={"path": path, "content": _content(path, task.strip(), wanted)})])
        if "run_check" in names and written and not checked:
            check = re.search(r"Available checks: ([a-z0-9_-]+)", next(
                t.description for t in tools if t.name == "run_check"))
            if check:
                return ChatResponse(tool_calls=[ToolCall(id="call_check", name="run_check",
                                                         arguments={"name": check.group(1)})])
        files = f" Files: {', '.join(written)}." if written else ""
        return f"[demo:{label}] {role} ({me}) finished the task.{files}"

    return reply


def demo_providers() -> tuple[dict[str, ScriptedProvider], list[ModelEntry]]:
    providers = {
        "demo-a": ScriptedProvider("demo-a", brain("alpha"), label="Demo A (offline)"),
        "demo-b": ScriptedProvider("demo-b", brain("beta"), label="Demo B (offline)"),
    }
    models = [
        ModelEntry("demo-a", "alpha-large", tier="strong", family="alpha", priority=10,
                   limits=Limits(rpm=600), trains="no"),
        ModelEntry("demo-b", "beta-large", tier="strong", family="beta", priority=20,
                   limits=Limits(rpm=600), trains="no"),
        ModelEntry("demo-a", "alpha-small", tier="fast", family="alpha", priority=30,
                   limits=Limits(rpm=600), trains="no"),
    ]
    return providers, models
