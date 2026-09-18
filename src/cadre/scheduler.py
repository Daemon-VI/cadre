"""Resume parked runs when `cadre serve` is not running (FR-9, FR-14.5, ADR-017).

The job runs `python -m cadre.scheduled` (`cadre resume --due`, logging to a file) every N minutes:
  * Windows — a Task Scheduler job running the environment's windowless pythonw;
  * Linux   — a systemd user timer (`~/.config/systemd/user/cadre-resume.{service,timer}`);
  * macOS   — a launchd agent (`~/Library/LaunchAgents/io.github.daemon-vi.cadre.resume.plist`).
Installing anything that runs on a schedule on the owner's machine needs the owner's explicit
yes, so the CLI shows every command and file first and asks. The unit files are produced by the
pure functions below, so they are tested on every platform.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

TASK_NAME = "Cadre - resume parked runs"
UNIT = "cadre-resume"
LABEL = "io.github.daemon-vi.cadre.resume"


def _check_every(every_minutes: int) -> None:
    if not 5 <= every_minutes <= 1440:
        raise ValueError("every_minutes must be between 5 and 1440")


def job_argv(windowless: bool = False) -> list[str]:
    """This environment's interpreter, this Cadre, this CADRE_HOME."""
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):  # a standalone build: cadre itself, no `-m`
        argv = [str(exe), "scheduled-run"]
    else:
        if windowless and exe.with_name("pythonw.exe").exists():
            exe = exe.with_name("pythonw.exe")
        argv = [str(exe), "-m", "cadre.scheduled"]
    home = os.environ.get("CADRE_HOME")
    return argv + [home] if home else argv


def resume_command() -> str:
    """The Windows job's command line: pythonw, because a python.exe or a cmd wrapper would flash
    a console every N minutes."""
    argv = job_argv(windowless=True)
    head = 3 if argv[1] == "-m" else 2  # quote the executable and CADRE_HOME, not the flags
    return " ".join(f'"{a}"' if i in (0, head) else a for i, a in enumerate(argv))


# ---------------------------------------------------------------- Windows (Task Scheduler)
def install_args(every_minutes: int = 30) -> list[str]:
    _check_every(every_minutes)
    return ["schtasks", "/Create", "/SC", "MINUTE", "/MO", str(every_minutes), "/TN", TASK_NAME,
            "/TR", resume_command(), "/F"]


def uninstall_args() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def status_args() -> list[str]:
    return ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"]


# ---------------------------------------------------------------- Linux (systemd user timer)
def _systemd_quote(arg: str) -> str:
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def systemd_units(every_minutes: int = 30) -> dict[str, str]:
    _check_every(every_minutes)
    service = (
        "[Unit]\n"
        "Description=Cadre: resume parked runs whose daily limits have reset\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={' '.join(_systemd_quote(a) for a in job_argv())}\n"
    )
    timer = (
        "[Unit]\n"
        "Description=Cadre: check for due parked runs\n\n"
        "[Timer]\n"
        "OnBootSec=5min\n"
        f"OnUnitActiveSec={every_minutes}min\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )
    return {f"{UNIT}.service": service, f"{UNIT}.timer": timer}


def systemd_dir(user_home: Path | None = None) -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base and user_home is None else (user_home or Path.home()) / ".config"
    return root / "systemd" / "user"


# ---------------------------------------------------------------- macOS (launchd agent)
def launchd_plist(every_minutes: int = 30, log_dir: Path | None = None) -> str:
    _check_every(every_minutes)
    doc: dict = {"Label": LABEL, "ProgramArguments": job_argv(), "StartInterval": every_minutes * 60,
                 "RunAtLoad": False, "ProcessType": "Background"}
    if log_dir is not None:  # launchd's own output; the job logs to CADRE_HOME/logs itself
        doc["StandardErrorPath"] = str(log_dir / "launchd.err.log")
    return plistlib.dumps(doc).decode("utf-8")


def launchd_path(user_home: Path | None = None) -> Path:
    return (user_home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


# ---------------------------------------------------------------- one plan for every platform
@dataclass
class Plan:
    files: dict[Path, str] = field(default_factory=dict)
    commands: list[list[str]] = field(default_factory=list)
    remove: list[Path] = field(default_factory=list)
    tolerate: set[int] = field(default_factory=set)  # indexes of commands allowed to fail

    def describe(self) -> str:
        lines = [f"  write {p}" for p in self.files] + [f"  delete {p}" for p in self.remove]
        lines += ["  " + subprocess.list2cmdline(c) for c in self.commands]
        return "\n".join(lines)


def install_plan(every_minutes: int = 30, platform: str = sys.platform,
                 user_home: Path | None = None) -> Plan:
    if platform == "win32":
        return Plan(commands=[install_args(every_minutes)])
    if platform == "darwin":
        path = launchd_path(user_home)
        return Plan(files={path: launchd_plist(every_minutes)},
                    commands=[["launchctl", "unload", "-w", str(path)], ["launchctl", "load", "-w", str(path)]],
                    tolerate={0})
    d = systemd_dir(user_home)
    return Plan(files={d / name: text for name, text in systemd_units(every_minutes).items()},
                commands=[["systemctl", "--user", "daemon-reload"],
                          ["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"]])


def uninstall_plan(platform: str = sys.platform, user_home: Path | None = None) -> Plan:
    if platform == "win32":
        return Plan(commands=[uninstall_args()])
    if platform == "darwin":
        path = launchd_path(user_home)
        return Plan(commands=[["launchctl", "unload", "-w", str(path)]], remove=[path], tolerate={0})
    d = systemd_dir(user_home)
    return Plan(commands=[["systemctl", "--user", "disable", "--now", f"{UNIT}.timer"],
                          ["systemctl", "--user", "daemon-reload"]],
                remove=[d / f"{UNIT}.service", d / f"{UNIT}.timer"], tolerate={0})


def status_command(platform: str = sys.platform) -> list[str]:
    if platform == "win32":
        return status_args()
    if platform == "darwin":
        return ["launchctl", "list", LABEL]
    return ["systemctl", "--user", "list-timers", f"{UNIT}.timer", "--all", "--no-pager"]


def run(args: list[str]) -> tuple[bool, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return False, f"{args[0]} is not available on this machine"
    return p.returncode == 0, (p.stdout or p.stderr).strip()


def apply(plan: Plan) -> tuple[bool, str]:
    """Write files, run commands in order, delete files; stop at the first real failure."""
    out: list[str] = []
    for path, text in plan.files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        out.append(f"wrote {path}")
    for i, cmd in enumerate(plan.commands):
        ok, text = run(cmd)
        if text:
            out.append(text)
        if not ok and i not in plan.tolerate:
            return False, "\n".join(out)
    for path in plan.remove:
        if path.exists():
            path.unlink()
            out.append(f"deleted {path}")
    return True, "\n".join(out)
