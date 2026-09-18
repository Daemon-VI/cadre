"""What the scheduled task runs: `pythonw -m cadre.scheduled [CADRE_HOME]` (FR-9, ADR-017).

It is `cadre resume --due --quiet`, adapted to pythonw: there is no console, so nothing flashes on
screen, and no stdout or stderr either, so both go to `<home>/logs/scheduler.log` (rolled to
`.log.1` past 1 MB).
"""

from __future__ import annotations

import os
import sys
import time
import traceback

LOG_MAX_BYTES = 1_000_000


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        os.environ["CADRE_HOME"] = argv[0]
    from .config import Home

    log = Home().root / "logs" / "scheduler.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
        log.replace(log.with_name(log.name + ".1"))
    saved = sys.stdout, sys.stderr
    with open(log, "a", encoding="utf-8") as f:
        sys.stdout = sys.stderr = f
        f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} resume --due\n")
        code = 1
        try:
            from .cli import app

            app(["resume", "--due", "--quiet"], prog_name="cadre")
            code = 0
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
        except BaseException:
            f.write(traceback.format_exc())
        finally:
            f.write(f"exit {code}\n")
            f.flush()
            sys.stdout, sys.stderr = saved
    return code


if __name__ == "__main__":
    raise SystemExit(main())
