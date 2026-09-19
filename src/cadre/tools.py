"""The tools an agent may call, and the named checks behind `run_check` (FR-5, ADR-006, ADR-014).

Eight tools, each in a permission class:

    read   list_files, read_file, search
    write  write_file, edit_file, post_note
    exec   run_check          — runs a check *named in the org file*; never a model-written command
    human  ask_human          — pauses the agent until the operator answers

An agent is offered only the tools its spec lists; a call to any other tool is refused and the
refusal is shown to the model as an observation.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import containers
from .org import CheckSpec
from .secrets import REDACTOR, scrubbed_env
from .types import ToolCall, ToolSpec
from .workspace import WorkspaceError

if TYPE_CHECKING:
    from .engine import RunContext

MAX_OBSERVATION = 4000
CHECK_OUTPUT_CAP = 16_000


@dataclass
class Tool:
    name: str
    perm: str
    spec: ToolSpec
    handler: Callable[[RunContext, str, str, dict[str, Any]], Awaitable[str]]


def _spec(name: str, description: str, props: dict[str, Any], required: list[str]) -> ToolSpec:
    return ToolSpec(name=name, description=description,
                    parameters={"type": "object", "properties": props, "required": required})


def _str(args: dict[str, Any], key: str, default: str | None = None) -> str:
    v = args.get(key, default)
    if v is None:
        raise ValueError(f"missing argument {key!r}")
    return v if isinstance(v, str) else str(v)


async def _list_files(ctx: RunContext, agent: str, step: str, args: dict[str, Any]) -> str:
    return ctx.workspace.listing(limit=200)


def _line(args: dict[str, Any], key: str) -> int | None:
    v = args.get(key)
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a whole number") from None


async def _read_file(ctx: RunContext, agent: str, step: str, args: dict[str, Any]) -> str:
    return ctx.workspace.read(_str(args, "path"), start_line=_line(args, "start_line"),
                              end_line=_line(args, "end_line"))


async def _edit_file(ctx: RunContext, agent: str, step: str, args: dict[str, Any]) -> str:
    info = ctx.workspace.edit(_str(args, "path"), _str(args, "old"), _str(args, "new", ""), agent,
                              line=_line(args, "line"))
    ctx.note_file(agent, step, str(info["path"]))
    return (f"edited {info['path']} (version {info['version']}): replaced {info['removed_lines']} "
            f"line(s) with {info['added_lines']}")


async def _search(ctx: RunContext, agent: str, step: str, args: dict[str, Any]) -> str:
    glob = args.get("glob")
    return ctx.workspace.search(_str(args, "pattern"), str(glob) if glob else None)


async def _write_file(ctx: RunContext, agent: str, step: str, args: dict[str, Any]) -> str:
    info = ctx.workspace.write(_str(args, "path"), _str(args, "content"), agent)
    ctx.note_file(agent, step, str(info["path"]))
    return f"wrote {info['path']} (version {info['version']}, {info['bytes']} bytes)"


async def _post_note(ctx: RunContext, agent: str, step: str, args: dict[str, Any]) -> str:
    text = _str(args, "text").strip()
    if not text:
        raise ValueError("empty note")
    ctx.post_note(agent, step, text[:1500])
    return "posted to the team board"


async def _run_check(ctx: RunContext, agent: str, step: str, args: dict[str, Any]) -> str:
    result = await ctx.run_check(_str(args, "name"), agent, step)
    return result.summary(3000)


async def _ask_human(ctx: RunContext, agent: str, step: str, args: dict[str, Any]) -> str:
    answer = await ctx.ask_human(_str(args, "question")[:1000], agent, step)
    return f"The operator answered: {answer}"


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("list_files", "read", _spec(
        "list_files", "List every file in the shared workspace with its size.", {}, []), _list_files),
    Tool("read_file", "read", _spec(
        "read_file", "Read a text file from the shared workspace, optionally only some lines.",
        {"path": {"type": "string", "description": "path relative to the workspace"},
         "start_line": {"type": "integer", "description": "first line (1-based), optional"},
         "end_line": {"type": "integer", "description": "last line, optional"}},
        ["path"]), _read_file),
    Tool("search", "read", _spec(
        "search", "Find lines matching a regex in workspace files; returns path:line: text.",
        {"pattern": {"type": "string"},
         "glob": {"type": "string", "description": "optional file filter, e.g. *.py"}},
        ["pattern"]), _search),
    Tool("edit_file", "write", _spec(
        "edit_file", "Replace one exact, unique piece of text in a file. Prefer this to "
        "rewriting a whole file.",
        {"path": {"type": "string"},
         "old": {"type": "string", "description": "exact text now in the file; must occur once"},
         "new": {"type": "string", "description": "replacement text"},
         "line": {"type": "integer", "description": "only if `old` occurs more than once: the line "
                  "where the one to replace starts"}},
        ["path", "old", "new"]), _edit_file),
    Tool("write_file", "write", _spec(
        "write_file", "Create or overwrite a text file in the shared workspace. Write the "
        "complete file content every time.",
        {"path": {"type": "string", "description": "path relative to the workspace"},
         "content": {"type": "string", "description": "the full file content"}},
        ["path", "content"]), _write_file),
    Tool("post_note", "write", _spec(
        "post_note", "Post a short note every teammate will see (decisions, blockers, handoffs).",
        {"text": {"type": "string"}}, ["text"]), _post_note),
    Tool("run_check", "exec", _spec(
        "run_check", "Run one of the organisation's named checks (tests, linters) on the "
        "workspace and get its result.",
        {"name": {"type": "string", "description": "the check's name"}}, ["name"]), _run_check),
    Tool("ask_human", "human", _spec(
        "ask_human", "Ask the human operator a question and wait for the answer. Use only when "
        "you are blocked on a decision you must not make yourself.",
        {"question": {"type": "string"}}, ["question"]), _ask_human),
]}


def specs_for(names: list[str], ctx: RunContext | None = None) -> list[ToolSpec]:
    specs = []
    for n in names:
        t = TOOLS.get(n)
        if t is None:
            continue
        if n == "run_check" and ctx is not None:
            checks = ", ".join(f"{c.name} ({c.description or ' '.join(c.command)})"
                               for c in ctx.org.checks)
            spec = t.spec.model_copy(deep=True)
            spec.description += f" Available checks: {checks}."
            specs.append(spec)
        else:
            specs.append(t.spec)
    return specs


def truncate(text: str, limit: int = MAX_OBSERVATION) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [cut: {len(text) - limit} more characters]"


async def execute(ctx: RunContext, agent: str, allowed: list[str], call: ToolCall,
                  step: str) -> tuple[bool, str]:
    """Run one tool call. Returns (ok, observation). Never raises for a tool-level problem."""
    tool = TOOLS.get(call.name)
    if tool is None or call.name not in allowed:
        ok, text = False, (f"error: tool {call.name!r} is not available to you. "
                           f"Your tools: {', '.join(allowed) or 'none'}.")
    else:
        try:
            text = await tool.handler(ctx, agent, step, call.arguments)
            ok = True
        except (WorkspaceError, ValueError, KeyError) as e:
            ok, text = False, f"error: {e}"
    text = REDACTOR.redact(text)
    brief = {k: (v if len(str(v)) < 120 else f"{str(v)[:117]}…") for k, v in call.arguments.items()}
    ctx.emit("agent.tool", agent=agent, step=step, tool=call.name, args=brief, ok=ok,
             result=truncate(text, 600))
    return ok, truncate(text)


# --------------------------------------------------------------------------- checks

@dataclass
class CheckResult:
    name: str
    passed: bool
    exit_code: int | None
    output: str
    seconds: float
    note: str = ""
    #: where it ran (FR-22): "subprocess", "docker" or "podman"
    runner: str = "subprocess"
    #: container checks: the image, its local id and the limits used
    container: dict[str, Any] | None = None

    def summary(self, limit: int = 1500) -> str:
        head = f"check {self.name}: {'PASSED' if self.passed else 'FAILED'}"
        if self.exit_code is not None:
            head += f" (exit {self.exit_code}, {self.seconds:.1f}s)"
        if self.runner != "subprocess":
            head += f" [in {self.runner}]"
        if self.note:
            head += f" — {self.note}"
        tail = self.output[-limit:] if self.output else ""
        return f"{head}\n{tail}".rstrip()

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "exit_code": self.exit_code,
                "seconds": round(self.seconds, 2), "note": self.note,
                "output_tail": self.output[-1500:], "runner": self.runner,
                **({"container": self.container} if self.container else {})}


def python_for_checks() -> str:
    """`{python}` means the interpreter Cadre runs under, so stdlib checks need no setup. A frozen
    standalone build has no interpreter of its own (`sys.executable` is cadre itself), so there it
    means the first Python on PATH."""
    if getattr(sys, "frozen", False):
        return shutil.which("python3") or shutil.which("python") or "python3"
    return sys.executable


def resolve_command(command: list[str]) -> list[str]:
    python = python_for_checks()
    return [python if part == "{python}" else part for part in command]


def _run_blocking(cmd: list[str], cwd: Path, timeout: int) -> tuple[int | None, str, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, env=scrubbed_env({"PYTHONDONTWRITEBYTECODE": "1"}),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=timeout)
        out = p.stdout.decode("utf-8", errors="replace")
        return p.returncode, out, ""
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", errors="replace")
        return None, out, f"timed out after {timeout}s"
    except FileNotFoundError:
        return None, "", f"command not found: {cmd[0]}"
    except OSError as e:
        return None, "", f"could not start: {e}"


async def run_check_process(spec: CheckSpec, cwd: Path, container_name: str = "") -> CheckResult:
    """Run a declared check, as the owner or in its container (FR-22). A thread keeps this
    independent of the event loop's subprocess support (uvicorn on Windows may run a selector
    loop, which cannot spawn processes). Timeout, output cap and exit code mean the same on both
    runners."""
    started = time.monotonic()
    container = None
    if spec.contained:
        name = container_name or containers.container_name("", spec.name, 0)
        code, out, note, container = await asyncio.to_thread(containers.run_contained, spec, cwd, name)
    else:
        code, out, note = await asyncio.to_thread(_run_blocking, resolve_command(spec.command),
                                                  cwd, spec.timeout)
    out = REDACTOR.redact(out)
    if len(out) > CHECK_OUTPUT_CAP:
        out = f"[… {len(out) - CHECK_OUTPUT_CAP} characters cut …]\n" + out[-CHECK_OUTPUT_CAP:]
    return CheckResult(spec.name, code == 0, code, out, time.monotonic() - started, note,
                       spec.runner, container)
