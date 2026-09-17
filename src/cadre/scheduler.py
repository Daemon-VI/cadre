"""Resume parked runs when `cadre serve` is not running (FR-9, ADR-017).

On Windows this registers a Task Scheduler job that runs `cadre resume --due` every N minutes —
the same mechanism Darkwatch uses for its daily scan. Installing anything that runs on a schedule
on the owner's machine needs the owner's explicit yes, so the CLI always shows the exact command
and asks first.
"""

from __future__ import annotations

import os
import subprocess
import sys

TASK_NAME = "Cadre - resume parked runs"


def resume_command() -> str:
    """The command the job runs: this interpreter, this Cadre, this CADRE_HOME."""
    cmd = f'"{sys.executable}" -m cadre.cli resume --due --quiet'
    home = os.environ.get("CADRE_HOME")
    if home:
        cmd = f'cmd /c "set "CADRE_HOME={home}" && {cmd}"'
    return cmd


def install_args(every_minutes: int = 30) -> list[str]:
    if not 5 <= every_minutes <= 1440:
        raise ValueError("every_minutes must be between 5 and 1440")
    return ["schtasks", "/Create", "/SC", "MINUTE", "/MO", str(every_minutes), "/TN", TASK_NAME,
            "/TR", resume_command(), "/F"]


def uninstall_args() -> list[str]:
    return ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]


def status_args() -> list[str]:
    return ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"]


def cron_line(every_minutes: int = 30) -> str:
    """The equivalent for Linux/macOS, printed rather than installed."""
    return f"*/{every_minutes} * * * * {sys.executable} -m cadre.cli resume --due --quiet"


def run(args: list[str]) -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, f"Task Scheduler is Windows-only; add this to your crontab instead:\n{cron_line()}"
    p = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return p.returncode == 0, (p.stdout or p.stderr).strip()
