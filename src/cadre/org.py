"""The organisation file: agents, checks, budget, and a workflow tree (FR-3).

An org is data. Everything is validated — agent references, tools, checks, step ids — before a
single model call, and every error names its path in the file.

Workflow steps (`type`):
    agent        one agent does one task
    sequence     steps in order              (a bare list is a sequence)
    parallel     steps at once, optionally joined by an agent
    review_loop  builder -> checks -> reviewers -> feedback, up to max_rounds
    council      proposals -> critique -> options -> votes -> tally -> memo
    manager      manager plans a task graph, workers execute, manager integrates
    approval     wait for a human

`type` may be omitted when a step's keys make it obvious (`agent:` / `builder:` / `members:` /
`manager:` / `prompt:` / `steps:`).
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

SLUG = r"^[a-z][a-z0-9_-]{0,31}$"
KNOWN_TOOLS = ("list_files", "read_file", "write_file", "run_check", "post_note", "ask_human")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentSpec(_Strict):
    id: str = Field(pattern=SLUG)
    role: str
    instructions: str = ""
    tier: Literal["strong", "fast"] = "strong"
    allow_downgrade: bool = True
    model: str | None = None  # pin "provider/model"
    tools: list[str] = Field(default_factory=list)
    diverse_from: list[str] = Field(default_factory=list)
    max_turns: int = Field(8, ge=1, le=30)
    max_output_tokens: int = Field(1500, ge=64, le=16000)
    temperature: float = Field(0.3, ge=0, le=2)

    @field_validator("diverse_from", "tools", mode="before")
    @classmethod
    def _listify(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v


class CheckSpec(_Strict):
    name: str = Field(pattern=SLUG)
    command: list[str] = Field(min_length=1)
    description: str = ""
    timeout: int = Field(120, ge=1, le=1800)


class Budget(_Strict):
    max_calls: int = Field(80, ge=1)
    max_tokens: int = Field(300_000, ge=1000)
    max_minutes: float = Field(30, gt=0)
    max_parallel: int = Field(2, ge=1, le=16)


class _Step(_Strict):
    id: str | None = Field(None, pattern=SLUG)
    title: str = ""


class AgentStep(_Step):
    type: Literal["agent"] = "agent"
    agent: str
    task: str


class SequenceStep(_Step):
    type: Literal["sequence"] = "sequence"
    steps: list[Step] = Field(min_length=1)


class ParallelStep(_Step):
    type: Literal["parallel"] = "parallel"
    steps: list[Step] = Field(min_length=1)
    join: str | None = None
    join_task: str = "Combine the results above into one coherent answer for the goal."


class ReviewLoopStep(_Step):
    type: Literal["review_loop"] = "review_loop"
    builder: str
    reviewers: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    task: str
    max_rounds: int = Field(3, ge=1, le=10)
    rule: Literal["all", "majority", "any"] = "all"


class CouncilStep(_Step):
    type: Literal["council"] = "council"
    members: list[str] = Field(min_length=2)
    chair: str
    question: str = "{goal}"
    rounds: int = Field(0, ge=0, le=3)
    rule: Literal["majority", "supermajority", "unanimous", "plurality"] = "majority"
    max_options: int = Field(4, ge=2, le=8)


class ManagerStep(_Step):
    type: Literal["manager"] = "manager"
    manager: str
    workers: list[str] = Field(min_length=1)
    task: str = "{goal}"
    max_tasks: int = Field(8, ge=1, le=30)
    reviewer: str | None = None
    checks: list[str] = Field(default_factory=list)
    review_rounds: int = Field(2, ge=1, le=5)


class ApprovalStep(_Step):
    type: Literal["approval"] = "approval"
    prompt: str


Step = Annotated[
    AgentStep | SequenceStep | ParallelStep | ReviewLoopStep | CouncilStep | ManagerStep | ApprovalStep,
    Field(discriminator="type"),
]
for _m in (SequenceStep, ParallelStep):
    _m.model_rebuild()


class OrgSpec(_Strict):
    name: str
    description: str = ""
    #: private: never route to a provider whose free tier trains (or may train) on prompts
    privacy: Literal["standard", "private"] = "standard"
    agents: list[AgentSpec] = Field(min_length=1)
    checks: list[CheckSpec] = Field(default_factory=list)
    budget: Budget = Field(default_factory=Budget)
    workflow: Step

    def agent(self, aid: str) -> AgentSpec:
        for a in self.agents:
            if a.id == aid:
                return a
        raise KeyError(aid)

    def check(self, name: str) -> CheckSpec:
        for c in self.checks:
            if c.name == name:
                return c
        raise KeyError(name)


class OrgError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("invalid org:\n  - " + "\n  - ".join(errors))


# --------------------------------------------------------------------------- normalise + check

_INFER = [("builder", "review_loop"), ("members", "council"), ("manager", "manager"),
          ("prompt", "approval"), ("agent", "agent"), ("steps", "sequence")]


def _normalise(step: Any) -> Any:
    if isinstance(step, list):
        return {"type": "sequence", "steps": [_normalise(s) for s in step]}
    if not isinstance(step, dict):
        return step
    step = dict(step)
    if "type" not in step and isinstance(step.get("approval"), str):
        step["type"], step["prompt"] = "approval", step.pop("approval")
    if "type" not in step:
        for key, kind in _INFER:
            if key in step:
                step["type"] = kind
                break
    if isinstance(step.get("steps"), list):
        step["steps"] = [_normalise(s) for s in step["steps"]]
    return step


_REF = re.compile(r"\{out\.([a-z][a-z0-9_-]*)\}")


def _walk(step: Any, path: str):
    yield step, path
    for i, child in enumerate(getattr(step, "steps", []) or []):
        yield from _walk(child, f"{path}.steps[{i}]")


def _check_refs(org: OrgSpec) -> list[str]:
    errs: list[str] = []
    ids = [a.id for a in org.agents]
    agents = set(ids)
    checks = {c.name for c in org.checks}
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        errs.append(f"agents: duplicate id {dup!r}")
    cnames = [c.name for c in org.checks]
    for dup in sorted({c for c in cnames if cnames.count(c) > 1}):
        errs.append(f"checks: duplicate name {dup!r}")
    for i, a in enumerate(org.agents):
        for t in a.tools:
            if t not in KNOWN_TOOLS:
                errs.append(f"agents[{i}] ({a.id}).tools: unknown tool {t!r} "
                            f"(known: {', '.join(KNOWN_TOOLS)})")
        if "run_check" in a.tools and not org.checks:
            errs.append(f"agents[{i}] ({a.id}).tools: run_check needs at least one entry in checks")
        for d in a.diverse_from:
            if d not in agents:
                errs.append(f"agents[{i}] ({a.id}).diverse_from: unknown agent {d!r}")

    def need_agent(ref: str | None, where: str) -> None:
        if ref is not None and ref not in agents:
            errs.append(f"{where}: unknown agent {ref!r}")

    def need_checks(names: list[str], where: str) -> None:
        for n in names:
            if n not in checks:
                errs.append(f"{where}: unknown check {n!r}")

    step_ids: list[str] = []
    texts: list[tuple[str, str]] = []
    for step, path in _walk(org.workflow, "workflow"):
        if step.id:
            step_ids.append(step.id)
        match step:
            case AgentStep():
                need_agent(step.agent, f"{path}.agent")
                texts.append((f"{path}.task", step.task))
            case ParallelStep():
                need_agent(step.join, f"{path}.join")
            case ReviewLoopStep():
                need_agent(step.builder, f"{path}.builder")
                for j, r in enumerate(step.reviewers):
                    need_agent(r, f"{path}.reviewers[{j}]")
                need_checks(step.checks, f"{path}.checks")
                if not step.reviewers and not step.checks:
                    errs.append(f"{path}: a review loop needs at least one reviewer or check")
                if step.builder in step.reviewers:
                    errs.append(f"{path}: the builder cannot review its own work")
                texts.append((f"{path}.task", step.task))
            case CouncilStep():
                for j, m in enumerate(step.members):
                    need_agent(m, f"{path}.members[{j}]")
                need_agent(step.chair, f"{path}.chair")
                if len(set(step.members)) != len(step.members):
                    errs.append(f"{path}.members: listed twice")
                texts.append((f"{path}.question", step.question))
            case ManagerStep():
                need_agent(step.manager, f"{path}.manager")
                for j, w in enumerate(step.workers):
                    need_agent(w, f"{path}.workers[{j}]")
                need_agent(step.reviewer, f"{path}.reviewer")
                need_checks(step.checks, f"{path}.checks")
                texts.append((f"{path}.task", step.task))
    for dup in sorted({s for s in step_ids if step_ids.count(s) > 1}):
        errs.append(f"workflow: duplicate step id {dup!r}")
    for where, text in texts:
        for ref in _REF.findall(text):
            if ref not in step_ids:
                errs.append(f"{where}: {{out.{ref}}} refers to no step with id {ref!r}")
    return errs


_KINDS = ("agent", "sequence", "parallel", "review_loop", "council", "manager", "approval")


def _friendly(err: dict[str, Any]) -> str:
    # pydantic names the union member in the location ("workflow.sequence.steps.2"); drop it
    loc = [str(p) for p in err["loc"] if p not in _KINDS]
    where = ".".join(loc)
    if err["type"] in ("union_tag_not_found", "union_tag_invalid"):
        return (f"{where}: a step needs a type ({', '.join(_KINDS)}), or one of the keys "
                "agent / builder / members / manager / approval / steps")
    return f"{where}: {err['msg']}"


def parse_org(data: dict[str, Any]) -> OrgSpec:
    if not isinstance(data, dict):
        raise OrgError(["the file must be a mapping with name, agents and workflow"])
    data = dict(data)
    if "workflow" in data:
        data["workflow"] = _normalise(data["workflow"])
    try:
        org = OrgSpec.model_validate(data)
    except ValidationError as e:
        raise OrgError([_friendly(err) for err in e.errors()]) from None
    errs = _check_refs(org)
    if errs:
        raise OrgError(errs)
    return org


def load_org_text(text: str) -> OrgSpec:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise OrgError([f"YAML: {e}"]) from None
    return parse_org(data)


def template_names() -> list[str]:
    root = resources.files("cadre") / "templates"
    return sorted(p.name[:-5] for p in root.iterdir() if p.name.endswith(".yaml"))


def template_text(name: str) -> str:
    return (resources.files("cadre") / "templates" / f"{name}.yaml").read_text(encoding="utf-8")


def find_org_text(name_or_path: str, orgs_dir: Path | None = None) -> tuple[str, str]:
    """(source description, YAML text) for a path, a user org name, or a template name."""
    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return str(p), p.read_text(encoding="utf-8")
    if orgs_dir is not None:
        for ext in (".yaml", ".yml"):
            q = orgs_dir / f"{name_or_path}{ext}"
            if q.exists():
                return str(q), q.read_text(encoding="utf-8")
    if name_or_path in template_names():
        return f"template:{name_or_path}", template_text(name_or_path)
    raise FileNotFoundError(f"no org called {name_or_path!r} (templates: {', '.join(template_names())})")


_VAR = re.compile(r"\{(goal|prev|out\.[a-z][a-z0-9_-]*)\}")


def render(text: str, variables: dict[str, str]) -> str:
    """Fill `{goal}`, `{prev}` and `{out.<step id>}`; leave every other brace alone (code!)."""
    return _VAR.sub(lambda m: variables.get(m.group(1), m.group(0)), text)
