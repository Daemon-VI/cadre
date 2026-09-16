"""A run's shared files: confined, capped, versioned (FR-5.1, FR-5.2).

Agents address files by relative path. Anything that could land outside the workspace is
refused before touching the disk: absolute paths, drive letters, `..`, `~`, NTFS alternate
data streams (`name:stream`), Windows device names, and symlinks that resolve elsewhere.
Every write also snapshots the content under `versions/`, so a reviewer's "it was fine two
rounds ago" can be checked.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from pathlib import Path

MAX_FILE_BYTES = 256_000
MAX_FILES = 300
MAX_READ_CHARS = 24_000

_DEVICE = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.I)
_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules", ".venv"}


class WorkspaceError(ValueError):
    pass


OnWrite = Callable[[str, int, str, int, str], None]  # path, version, sha256, bytes, agent


class Workspace:
    def __init__(self, run_dir: Path, on_write: OnWrite | None = None):
        self.root = run_dir / "workspace"
        self.versions_dir = run_dir / "versions"
        self.root.mkdir(parents=True, exist_ok=True)
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        self._on_write = on_write
        self._versions: dict[str, int] = {}
        for snap in self.versions_dir.iterdir():
            name, _, v = snap.name.rpartition("@")
            if v.isdigit():
                self._versions[name] = max(self._versions.get(name, 0), int(v))

    def resolve(self, rel: str) -> tuple[Path, str]:
        if not isinstance(rel, str) or not rel.strip():
            raise WorkspaceError("a file path is required")
        clean = rel.strip().replace("\\", "/")
        if clean.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", clean):
            raise WorkspaceError(f"{rel!r}: only paths relative to the workspace are allowed")
        parts = [p for p in clean.split("/") if p not in ("", ".")]
        if not parts:
            raise WorkspaceError(f"{rel!r} is the workspace itself, not a file")
        for p in parts:
            if p == "..":
                raise WorkspaceError(f"{rel!r}: '..' is not allowed")
            if ":" in p or _DEVICE.match(p) or p.endswith((" ", ".")) or p == ".git":
                raise WorkspaceError(f"{rel!r}: {p!r} is not a permitted file name")
        target = self.root.joinpath(*parts)
        root_real = self.root.resolve()
        if not target.resolve().is_relative_to(root_real):
            raise WorkspaceError(f"{rel!r} resolves outside the workspace")
        return target, "/".join(parts)

    def files(self) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and not (_SKIP_DIRS & set(p.relative_to(self.root).parts)):
                out.append((p.relative_to(self.root).as_posix(), p.stat().st_size))
        return out

    def listing(self, limit: int = 40) -> str:
        files = self.files()
        if not files:
            return "(empty)"
        lines = [f"{path} ({size} B)" for path, size in files[:limit]]
        if len(files) > limit:
            lines.append(f"… and {len(files) - limit} more")
        return "\n".join(lines)

    def read(self, rel: str, max_chars: int = MAX_READ_CHARS) -> str:
        target, name = self.resolve(rel)
        if not target.is_file():
            raise WorkspaceError(f"{name}: no such file")
        data = target.read_bytes()
        if b"\x00" in data[:4096]:
            raise WorkspaceError(f"{name}: binary file ({len(data)} B) — not readable as text")
        text = data.decode("utf-8", errors="replace")
        if len(text) > max_chars:
            return text[:max_chars] + f"\n… [truncated: {len(text) - max_chars} more characters]"
        return text

    def write(self, rel: str, content: str, agent: str) -> dict[str, object]:
        target, name = self.resolve(rel)
        if not isinstance(content, str):
            raise WorkspaceError("content must be text")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceError(f"{name}: {len(data)} B exceeds the {MAX_FILE_BYTES} B file limit")
        if target.is_dir():
            raise WorkspaceError(f"{name} is a directory")
        if not target.exists() and len(self.files()) >= MAX_FILES:
            raise WorkspaceError(f"the workspace already holds {MAX_FILES} files")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        version = self._versions.get(name.replace("/", "__"), 0) + 1
        self._versions[name.replace("/", "__")] = version
        (self.versions_dir / f"{name.replace('/', '__')}@{version}").write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        if self._on_write:
            self._on_write(name, version, sha, len(data), agent)
        return {"path": name, "version": version, "bytes": len(data), "sha256": sha[:12]}
