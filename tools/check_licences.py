"""Fail on a copyleft runtime dependency (FR-14.1, ADR-030).

Run in an environment holding only the package and its runtime dependencies:
    uv run --isolated --no-project --with . --with pip-licenses python tools/check_licences.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

PERMISSIVE = ("MIT", "BSD", "Apache", "ISC", "PSF", "Python Software Foundation", "Unlicense", "0BSD")
COPYLEFT = re.compile(r"\b(A?GPL|LGPL|SSPL|EUPL|CDDL|EPL|OSL|CPAL|MPL)\b|General Public|Mozilla|Affero|"
                      r"Commons Clause", re.IGNORECASE)
# Reviewed by name. certifi's MPL-2.0 is file-level copyleft; Cadre uses it unmodified (ADR-030).
EXCEPTIONS = {"certifi": "MPL-2.0, file-level copyleft, used unmodified"}
# The checker itself and its own dependencies are not shipped.
TOOLING = {"cadre-ai", "pip-licenses", "prettytable", "wcwidth", "tomli"}


def main() -> int:
    raw = subprocess.run([sys.executable, "-m", "piplicenses", "--from=mixed", "--format=json"],
                         capture_output=True, check=True, encoding="utf-8").stdout
    failed = False
    for pkg in sorted(json.loads(raw), key=lambda p: p["Name"].lower()):
        name, lic = pkg["Name"], pkg["License"]
        if name.lower() in TOOLING:
            continue
        if name.lower() in EXCEPTIONS:
            print(f"ok   {name}: {lic} (reviewed: {EXCEPTIONS[name.lower()]})")
        elif COPYLEFT.search(lic):
            print(f"FAIL {name}: {lic} is copyleft")
            failed = True
        elif not any(p.lower() in lic.lower() for p in PERMISSIVE):
            print(f"FAIL {name}: {lic!r} is not a known permissive licence; review it")
            failed = True
        else:
            print(f"ok   {name}: {lic}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
