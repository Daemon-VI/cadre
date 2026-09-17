"""Project mode: work on an existing git repository through a worktree (FR-8, NFR-10, ADR-016).

The owner's working tree and branches are never touched. A run gets
`git worktree add <run>/workspace -b cadre/<run-id> <base>`; the engine commits each finished
step there with the repository's own identity; the owner reviews the branch. Cadre never merges,
pushes, force-pushes, rewrites history, or deletes a branch it did not create.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .org import CheckSpec
from .secrets import scrubbed_env

CHECKS_FILE = ".cadre/checks.yaml"
PROTECTED = (".cadre",)


class ProjectError(ValueError):
    pass


def git(args: list[str], cwd: str | Path, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
    env = scrubbed_env({"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true", "LC_ALL": "C"})
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), env=env, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise ProjectError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise ProjectError(f"git {' '.join(args[:2])} timed out") from None
    if check and p.returncode != 0:
        raise ProjectError(f"git {' '.join(args[:3])} failed: {(p.stderr or p.stdout).strip()[:400]}")
    return p


@dataclass
class ProjectInfo:
    root: str
    base: str          # commit sha the branch starts from
    base_label: str    # what the owner asked for (a branch name, or HEAD)
    head_branch: str   # the owner's current branch at creation, for the record
    dirty: bool

    def as_dict(self) -> dict[str, Any]:
        return vars(self).copy()


def inspect(path: str | Path, base: str | None = None, allow_dirty: bool = False) -> ProjectInfo:
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ProjectError(f"{p} is not a directory")
    top = git(["rev-parse", "--show-toplevel"], p, check=False)
    if top.returncode != 0:
        raise ProjectError(f"{p} is not a git repository")
    root = str(Path(top.stdout.strip()).resolve())
    if git(["rev-parse", "--verify", "--quiet", "HEAD"], root, check=False).returncode != 0:
        raise ProjectError(f"{root} has no commits yet; commit something first")
    dirty = bool(git(["status", "--porcelain"], root).stdout.strip())
    if dirty and not allow_dirty:
        raise ProjectError(f"{root} has uncommitted changes. Commit or stash them, or pass "
                           "--allow-dirty (the run then starts from HEAD and does not see them)")
    label = base or "HEAD"
    rev = git(["rev-parse", "--verify", "--quiet", f"{label}^{{commit}}"], root, check=False)
    if rev.returncode != 0:
        raise ProjectError(f"base {label!r} is not a commit or branch in {root}")
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"], root).stdout.strip()
    return ProjectInfo(root, rev.stdout.strip(), label, branch, dirty)


def repo_checks(root: str, sha: str) -> list[dict[str, Any]]:
    """Checks declared in `.cadre/checks.yaml` **at the base commit** (never the live worktree)."""
    shown = git(["show", f"{sha}:{CHECKS_FILE}"], root, check=False)
    if shown.returncode != 0:
        return []
    try:
        data = yaml.safe_load(shown.stdout) or {}
        items = data.get("checks", []) if isinstance(data, dict) else []
        return [CheckSpec.model_validate(c).model_dump() for c in items]
    except (yaml.YAMLError, ValidationError, AttributeError) as e:
        raise ProjectError(f"{CHECKS_FILE} at {sha[:10]} is invalid: {str(e)[:300]}") from None


def branch_exists(root: str, branch: str) -> bool:
    return git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], root, check=False).returncode == 0


def is_worktree(workspace: Path) -> bool:
    return (workspace / ".git").is_file()


def ensure_worktree(root: str, base: str, branch: str, workspace: Path) -> str:
    """Create the run's worktree, or reuse it on resume. Returns 'created' or 'reused'."""
    if is_worktree(workspace):
        current = git(["rev-parse", "--abbrev-ref", "HEAD"], workspace).stdout.strip()
        if current != branch:
            raise ProjectError(f"{workspace} is on {current}, expected {branch}")
        return "reused"
    if workspace.exists() and any(workspace.iterdir()):
        raise ProjectError(f"{workspace} exists and is not this run's worktree")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    if workspace.exists():
        workspace.rmdir()
    if branch_exists(root, branch):
        # the worktree was removed (cleanup) but the branch survived: continue on it
        git(["worktree", "add", str(workspace), branch], root)
        return "reused"
    git(["worktree", "add", "-b", branch, str(workspace), base], root)
    return "created"


def commit_all(workspace: Path, message: str) -> str | None:
    """Commit everything that changed in the worktree. None when there was nothing to commit."""
    git(["add", "-A"], workspace)
    if git(["diff", "--cached", "--quiet"], workspace, check=False).returncode == 0:
        return None
    git(["commit", "-q", "-m", message], workspace)
    return git(["rev-parse", "HEAD"], workspace).stdout.strip()


def diff_stat(root: str, base: str, branch: str) -> str:
    return git(["diff", "--stat", f"{base}...{branch}"], root, check=False).stdout.strip()


def commits_on_branch(root: str, base: str, branch: str) -> list[str]:
    out = git(["log", "--format=%h %s", f"{base}..{branch}"], root, check=False).stdout.strip()
    return out.splitlines() if out else []


def review_commands(root: str, base: str, branch: str, workspace: Path) -> dict[str, str]:
    q = f'"{root}"'
    return {
        "log": f"git -C {q} log --oneline {base[:10]}..{branch}",
        "diff": f"git -C {q} diff {base[:10]}...{branch}",
        "discard": f'git -C {q} worktree remove "{workspace}" && git -C {q} branch -D {branch}',
    }


def remove_worktree(root: str, workspace: Path) -> tuple[bool, str]:
    """Remove a finished run's worktree without --force; the branch stays for review."""
    if not is_worktree(workspace):
        return False, "not a worktree (already removed?)"
    p = git(["worktree", "remove", str(workspace)], root, check=False)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout).strip()[:300]
    return True, "removed"


def owner_state(root: str) -> dict[str, str]:
    """What NFR-10 promises not to change."""
    return {
        "head": git(["rev-parse", "HEAD"], root).stdout.strip(),
        "branch": git(["rev-parse", "--abbrev-ref", "HEAD"], root).stdout.strip(),
        "status": git(["status", "--porcelain"], root).stdout,
    }
