"""The container runner for checks (FR-22, ADR-031).

A check with `runner: docker` or `runner: podman` runs in a throwaway container. The run's
workspace is its only mount, at /work. It has no network, a read-only root filesystem with a
small /tmp, no capabilities, no privilege escalation, a non-root user, and caps on processes,
memory and CPU. The command line is built here as a list, never through a shell, so a test can
assert every flag.

What this does NOT contain (ADR-031): the container shares the host's kernel, and the workspace
is mounted read-write, so a check can change or delete anything in it.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .org import PINNED_IMAGE, CheckSpec
from .secrets import REDACTOR, looks_secret, scrubbed_env

WORKDIR = "/work"
TMPFS = "/tmp:rw,nosuid,nodev,size=64m"
#: the user a container check runs as when the host has no uid to lend (Windows)
FALLBACK_USER = "10001:10001"
#: ... and when Cadre itself runs as root
NOBODY = "65534:65534"
#: `{python}` inside an image: the image's interpreter, not the one Cadre runs under
CONTAINER_PYTHON = "python3"
FIXED_ENV = {"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"}
INSPECT_TIMEOUT = 60


class ContainerError(Exception):
    """The check could not start in its container. The message says what to do about it."""


def container_user() -> str:
    """A non-root user: the owner's own uid on Linux and macOS, so files written in the workspace
    stay theirs; `nobody` when Cadre runs as root; 10001 on Windows."""
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return FALLBACK_USER
    uid = getuid()
    return NOBODY if uid == 0 else f"{uid}:{os.getgid()}"


def container_name(run_id: str, check: str, n: int) -> str:
    """Derived from the run id, so a timeout can kill it and `docker ps` shows whose it is. The
    suffix keeps a resumed run clear of a container its previous process left behind."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", f"cadre-{run_id or 'run'}-{check}-{n}-{secrets.token_hex(2)}")


def container_command(command: list[str]) -> list[str]:
    return [CONTAINER_PYTHON if part == "{python}" else part for part in command]


def passed_env(spec: CheckSpec, environ: Mapping[str, str] | None = None) -> list[str]:
    """The allowlisted names that are set. Credential-like names and any value Cadre knows to be a
    key are dropped, whatever the org file says."""
    env = os.environ if environ is None else environ
    known = REDACTOR.known()
    return [n for n in spec.env if n in env and not looks_secret(n) and env[n] not in known]


def mount_source(workspace: Path) -> str:
    """The one directory the container sees: the run's workspace, and never anything wider."""
    p = Path(workspace).resolve()
    home = Path.home().resolve()
    if p == Path(p.anchor) or p == home or p in home.parents:
        raise ContainerError(f"refusing to mount {p}: only a run's workspace is mounted")
    cadre_home = os.environ.get("CADRE_HOME")
    if cadre_home and (p == Path(cadre_home).resolve() or p in Path(cadre_home).resolve().parents):
        raise ContainerError(f"refusing to mount {p}: it holds Cadre's own state")
    if (p / ".git").is_dir():
        # a project run's worktree has a .git *file* that points outside the mount; a directory
        # means a repository's own history (and hooks) would be writable from the check
        raise ContainerError(f"refusing to mount {p}: it contains a repository's .git directory")
    if "," in str(p):
        raise ContainerError(f"cannot mount {p}: the path contains a comma, which --mount cannot take")
    return str(p)


def build_command(runtime: str, spec: CheckSpec, workspace: Path, name: str,
                  env_names: list[str] | None = None, user: str | None = None) -> list[str]:
    """The full `docker run` / `podman run` argument list for one check."""
    user = user or container_user()
    argv = [runtime, "run", "--rm", "--name", name, "--pull", "never",
            "--network", "none",
            "--read-only", "--tmpfs", TMPFS,
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", str(spec.pids_limit),
            "--memory", spec.memory, "--memory-swap", spec.memory,  # equal: no swap
            "--cpus", f"{spec.cpus:g}",
            "--user", user,
            "--mount", f"type=bind,source={mount_source(workspace)},target={WORKDIR}",
            "--workdir", WORKDIR]
    if runtime == "podman" and user not in (FALLBACK_USER, NOBODY):
        # rootless Podman maps the owner's uid to root unless told to keep it
        argv += ["--userns", "keep-id"]
    for k, v in FIXED_ENV.items():
        argv += ["--env", f"{k}={v}"]
    for n in env_names or []:
        argv += ["--env", n]  # by name only: the value never appears on a command line
    return [*argv, str(spec.image), *container_command(spec.command)]


def describe(spec: CheckSpec) -> str:
    """Where a check runs, in the words an approver reads."""
    if not spec.contained:
        return "runs as you, on this machine, with your permissions"
    pin = "" if PINNED_IMAGE.match(spec.image or "") else " (NOT pinned by digest)"
    return (f"in {spec.runner}, image {spec.image}{pin}; no network, {spec.memory} memory, "
            f"{spec.cpus:g} CPUs, {spec.pids_limit} processes; only the workspace is mounted")


def limits(spec: CheckSpec, user: str | None = None) -> dict[str, Any]:
    return {"network": "none", "memory": spec.memory, "cpus": spec.cpus,
            "pids_limit": spec.pids_limit, "user": user or container_user(),
            "read_only_root": True}


def image_id(runtime: str, image: str) -> str:
    """The local image's id. Cadre never pulls: a pull reaches the network from the host."""
    try:
        p = subprocess.run([runtime, "image", "inspect", "--format", "{{.Id}}", image],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           env=scrubbed_env(), timeout=INSPECT_TIMEOUT)
    except FileNotFoundError:
        raise ContainerError(f"{runtime} is not installed (not found on PATH)") from None
    except subprocess.TimeoutExpired:
        raise ContainerError(f"{runtime} did not answer within {INSPECT_TIMEOUT}s; is it running?") from None
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip().splitlines()[0]
    err = (p.stderr or "").strip()
    if re.search(r"no such image|image not known|image not found", err, re.I):
        raise ContainerError(f"image {image} is not on this machine, and Cadre never pulls one "
                             f"(a pull uses the network). Run: {runtime} pull {image}")
    first = err.splitlines()[0] if err else f"exit {p.returncode}"
    raise ContainerError(f"{runtime} is not usable: {first}")


def exit_note(code: int, runtime: str) -> str:
    if code == 125:
        return f"{runtime} could not start the container (exit 125)"
    if code in (126, 127):
        return f"the command could not run in the image (exit {code})"
    if code == 137:
        return "killed (exit 137): usually the memory cap"
    return ""


def kill(runtime: str, name: str) -> None:
    try:
        subprocess.run([runtime, "kill", name], capture_output=True, stdin=subprocess.DEVNULL,
                       env=scrubbed_env(), timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_contained(spec: CheckSpec, workspace: Path, name: str) -> tuple[int | None, str, str, dict[str, Any]]:
    """Run one check in its container. Returns (exit code, output, note, what was used)."""
    user = container_user()
    info: dict[str, Any] = {"image": spec.image, "pinned": bool(PINNED_IMAGE.match(spec.image or "")),
                            "name": name, **limits(spec, user)}
    try:
        info["image_id"] = image_id(spec.runner, str(spec.image))
        argv = build_command(spec.runner, spec, workspace, name, passed_env(spec), user)
    except ContainerError as e:
        return None, "", str(e), info
    try:
        p = subprocess.run(argv, env=scrubbed_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, timeout=spec.timeout)
    except subprocess.TimeoutExpired as e:
        kill(spec.runner, name)
        out = (e.stdout or b"").decode("utf-8", errors="replace")
        return None, out, f"timed out after {spec.timeout}s; the container was killed", info
    except FileNotFoundError:
        return None, "", f"{spec.runner} is not installed (not found on PATH)", info
    except OSError as e:
        return None, "", f"could not start: {e}", info
    return p.returncode, p.stdout.decode("utf-8", errors="replace"), exit_note(p.returncode, spec.runner), info
