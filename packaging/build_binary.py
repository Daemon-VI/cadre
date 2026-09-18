"""Build the standalone one-folder binary with PyInstaller, then smoke-test it (FR-16.3).

Run in CI (`release.yml`), one runner per OS; this laptop is too small for the matrix:
    uv run --with pyinstaller python packaging/build_binary.py
Leaves `dist/cadre/` and an archive `dist/cadre-<version>-<os>-<arch>.(zip|tar.gz)`.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def run(cmd: list[str], **kw) -> str:
    print("$", " ".join(cmd), flush=True)
    p = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", **kw)
    if p.returncode != 0:
        print(p.stdout, p.stderr, sep="\n")
        raise SystemExit(f"failed ({p.returncode}): {' '.join(cmd)}")
    return p.stdout


def build() -> Path:
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--name", "cadre",
         "--distpath", str(DIST), "--workpath", str(ROOT / "build" / "pyinstaller"),
         "--specpath", str(ROOT / "build"),
         "--collect-data", "cadre", "--collect-data", "tzdata",
         "--collect-submodules", "uvicorn", "--collect-submodules", "keyring.backends",
         "--copy-metadata", "keyring", "--collect-submodules", "mcp",
         str(ROOT / "packaging" / "cadre_entry.py")], cwd=ROOT)
    return DIST / "cadre" / ("cadre.exe" if os.name == "nt" else "cadre")


def smoke(exe: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        env = {k: v for k, v in os.environ.items() if not k.startswith("CADRE_")}
        env.update(CADRE_HOME=str(Path(tmp) / "home"), CADRE_NO_KEYRING="1")
        version = run([str(exe), "--version"], env=env, cwd=tmp).strip()
        out = run([str(exe), "run", "decision-board", "Should we open a second office?", "--demo"],
                  env=env, cwd=tmp)
        if "succeeded" not in out:
            print(out)
            raise SystemExit("the demo run did not succeed in the standalone build")
        print(f"{version}: demo run succeeded")
        return version.split()[-1]


def archive(version: str) -> Path:
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")
    base = DIST / f"cadre-{version}-{system}-{machine}"
    fmt = "zip" if system == "windows" else "gztar"
    path = Path(shutil.make_archive(str(base), fmt, root_dir=DIST, base_dir="cadre"))
    print(f"{path.name}: {path.stat().st_size / 1024 / 1024:.1f} MiB")
    return path


if __name__ == "__main__":
    exe = build()
    archive(smoke(exe))
