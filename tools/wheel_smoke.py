"""Build the wheel, install it into a clean venv, and run a demo from there (FR-16.1).

Proves the dashboard (`web/`) and the org templates ship inside the package, and that both
command names work. Runs outside the source tree with a throwaway CADRE_HOME and no keychain.
Used by CI and by hand: `uv run python tools/wheel_smoke.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], **kw) -> str:
    print("$", " ".join(cmd), flush=True)
    p = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", **kw)
    if p.returncode != 0:
        print(p.stdout, p.stderr, sep="\n")
        raise SystemExit(f"failed ({p.returncode}): {' '.join(cmd)}")
    return p.stdout


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        run(["uv", "build", "--wheel", "--out-dir", str(tmp_path / "dist")], cwd=ROOT)
        [wheel] = (tmp_path / "dist").glob("*.whl")
        names = zipfile.ZipFile(wheel).namelist()
        for needed in ("cadre/web/index.html", "cadre/web/app.js", "cadre/templates/decision-board.yaml"):
            if needed not in names:
                raise SystemExit(f"{needed} is missing from {wheel.name}")
        print(f"{wheel.name}: {len(names)} files, {wheel.stat().st_size / 1024:.0f} KiB")

        venv = tmp_path / "venv"
        run(["uv", "venv", "--python", f"{sys.version_info.major}.{sys.version_info.minor}", str(venv)])
        bindir = venv / ("Scripts" if os.name == "nt" else "bin")
        python = bindir / ("python.exe" if os.name == "nt" else "python")
        run(["uv", "pip", "install", "--python", str(python), str(wheel)])

        env = {k: v for k, v in os.environ.items() if not k.startswith(("CADRE_", "VIRTUAL_ENV"))}
        env.update(CADRE_HOME=str(tmp_path / "home"), CADRE_NO_KEYRING="1", PYTHONUTF8="1")
        exe = lambda name: str(bindir / (name + (".exe" if os.name == "nt" else "")))  # noqa: E731
        print(run([exe("cadre"), "version"], env=env, cwd=tmp).strip())
        print(run([exe("cadre-ai"), "version"], env=env, cwd=tmp).strip())
        out = run([exe("cadre"), "run", "decision-board", "Should we open a second office?", "--demo"],
                  env=env, cwd=tmp)
        if "succeeded" not in out:
            print(out)
            raise SystemExit("the demo run did not succeed")
        print("demo run from the installed wheel: succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
