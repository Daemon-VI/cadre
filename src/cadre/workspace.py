"""A run's shared files: confined, capped, versioned (FR-5.1, FR-5.2).

Agents address files by relative path. Anything that could land outside the workspace is
refused before touching the disk: absolute paths, drive letters, `..`, `~`, NTFS alternate
data streams (`name:stream`), Windows device names, and symlinks that resolve elsewhere.
Every write also snapshots the content under `versions/`, so a reviewer's "it was fine two
rounds ago" can be checked.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from collections.abc import Callable
from pathlib import Path

MAX_FILE_BYTES = 256_000
MAX_FILES = 300
MAX_READ_CHARS = 24_000
MAX_SEARCH_HITS = 40
MAX_LISTED = 20_000


def _where_first_line_is(text: str, old: str) -> str:
    """A pointer for an edit whose `old` text is not in the file, usually a whitespace slip."""
    first = next((ln.strip() for ln in old.splitlines() if ln.strip()), "")
    hits = [n for n, ln in enumerate(text.splitlines(), 1) if first and ln.strip() == first]
    if not hits:
        return "copy it exactly from the file, including indentation"
    where = ", ".join(map(str, hits[:5]))
    return (f"its first line is at line {where}; read those lines again and copy them exactly, "
            "including indentation")

_DEVICE = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.I)
_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules", ".venv"}


class WorkspaceError(ValueError):
    pass


OnWrite = Callable[[str, int, str, int, str], None]  # path, version, sha256, bytes, agent


class Workspace:
    def __init__(self, run_dir: Path, on_write: OnWrite | None = None,
                 protected: tuple[str, ...] = ()):
        self.root = run_dir / "workspace"
        #: top-level names agents may read but never write (project mode: `.cadre`, ADR-016)
        self.protected = protected
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

    def files(self, limit: int = MAX_LISTED) -> list[tuple[str, int]]:
        """Every file under the root, pruning tool directories instead of walking into them."""
        out: list[tuple[str, int]] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
            base = Path(dirpath)
            for name in sorted(filenames):
                if name in _SKIP_DIRS:  # a worktree's `.git` is a file
                    continue
                full = base / name
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                out.append((full.relative_to(self.root).as_posix(), size))
                if len(out) >= limit:
                    return sorted(out)
        return sorted(out)

    def listing(self, limit: int = 40) -> str:
        files = self.files()
        if not files:
            return "(empty)"
        lines = [f"{path} ({size} B)" for path, size in files[:limit]]
        if len(files) > limit:
            lines.append(f"… and {len(files) - limit} more")
        return "\n".join(lines)

    def text(self, rel: str) -> tuple[str, str]:
        """(normalised name, full text) of a text file in the workspace."""
        target, name = self.resolve(rel)
        if not target.is_file():
            raise WorkspaceError(f"{name}: no such file")
        data = target.read_bytes()
        if b"\x00" in data[:4096]:
            raise WorkspaceError(f"{name}: binary file ({len(data)} B) — not readable as text")
        return name, data.decode("utf-8", errors="replace")

    def read(self, rel: str, max_chars: int = MAX_READ_CHARS, start_line: int | None = None,
             end_line: int | None = None) -> str:
        name, text = self.text(rel)
        if start_line is not None or end_line is not None:
            lines = text.splitlines(keepends=True)
            total = len(lines)
            first = max(1, int(start_line or 1))
            last = min(total, int(end_line or total))
            if first > total:
                raise WorkspaceError(f"{name} has {total} lines; line {first} does not exist")
            body = "".join(lines[first - 1:last])
            text = f"[{name}: lines {first}-{last} of {total}]\n{body}"
        if len(text) > max_chars:
            return text[:max_chars] + f"\n… [truncated: {len(text) - max_chars} more characters]"
        return text

    def _guard(self, name: str) -> None:
        top = name.split("/", 1)[0]
        if top in self.protected:
            raise WorkspaceError(f"{name}: files under {top}/ are the owner's configuration and "
                                 "cannot be changed by agents")

    def edit(self, rel: str, old: str, new: str, agent: str, line: int | None = None) -> dict[str, object]:
        """Replace exactly one exact occurrence of `old` (FR-13, ADR-021). When `old` occurs more
        than once, `line` picks the occurrence that starts nearest that line."""
        self._guard(self.resolve(rel)[1])
        if not isinstance(old, str) or not old:
            raise WorkspaceError("`old` must be the exact, non-empty text to replace")
        if not isinstance(new, str):
            raise WorkspaceError("`new` must be text")
        name, text = self.text(rel)
        count = text.count(old)
        if count == 0 and "\r\n" in text and "\r\n" not in old:
            # models echo files with plain newlines; match CRLF files the same way
            old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
            count = text.count(old)
        if count == 0:
            raise WorkspaceError(f"{name}: `old` matches 0 times; it must match exactly once — "
                                 f"{_where_first_line_is(text, old)}")
        at = 0
        if count > 1:
            # where each match starts, so a model can re-anchor instead of guessing (live, 2026-09-19:
            # two identical `raise NotImplementedError` stubs cost an engineer all of its turns)
            starts = [m.start() for m in re.finditer(re.escape(old), text)]
            lines = [text.count("\n", 0, s) + 1 for s in starts]
            listed = ", ".join(map(str, lines[:-1])) + f" and {lines[-1]}"
            if line is None:
                raise WorkspaceError(f"{name}: `old` matches {count} times, at lines {listed}; it must "
                                     "match exactly once — pass `line` with the line number of the one "
                                     "you mean, or include more surrounding lines")
            dist = sorted((abs(n - line), i) for i, n in enumerate(lines))
            if len(dist) > 1 and dist[0][0] == dist[1][0]:
                raise WorkspaceError(f"{name}: line {line} is as near to the match at line "
                                     f"{lines[dist[0][1]]} as to the one at line {lines[dist[1][1]]}; "
                                     f"`old` matches at lines {listed}")
            at = starts[dist[0][1]]
        else:
            at = text.index(old)
        info = self.write(name, text[:at] + new + text[at + len(old):], agent)
        info["removed_lines"] = old.count("\n") + 1
        info["added_lines"] = new.count("\n") + 1 if new else 0
        return info

    def search(self, pattern: str, glob: str | None = None, limit: int = MAX_SEARCH_HITS) -> str:
        """`path:line: text` for every matching line, capped, inside the workspace only."""
        if not isinstance(pattern, str) or not pattern:
            raise WorkspaceError("a search pattern is required")
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = re.compile(re.escape(pattern))
        hits: list[str] = []
        total = 0
        for path, size in self.files():
            if glob and not (fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path.rsplit("/", 1)[-1], glob)):
                continue
            if size > MAX_FILE_BYTES:
                continue
            try:
                _, text = self.text(path)
            except WorkspaceError:
                continue
            for no, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    total += 1
                    if len(hits) < limit:
                        hits.append(f"{path}:{no}: {line.strip()[:200]}")
        if not hits:
            return f"no matches for {pattern!r}" + (f" in {glob}" if glob else "")
        if total > len(hits):
            hits.append(f"… {total - len(hits)} more matches not shown ({total} total); narrow the pattern or glob")
        return "\n".join(hits)

    def write(self, rel: str, content: str, agent: str) -> dict[str, object]:
        target, name = self.resolve(rel)
        self._guard(name)
        if not isinstance(content, str):
            raise WorkspaceError("content must be text")
        data = content.encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise WorkspaceError(f"{name}: {len(data)} B exceeds the {MAX_FILE_BYTES} B file limit")
        if target.is_dir():
            raise WorkspaceError(f"{name} is a directory")
        if not target.exists() and len(self._versions) >= MAX_FILES:
            # counts files written during this run, not files a project already had
            raise WorkspaceError(f"this run has already written {MAX_FILES} different files")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        version = self._versions.get(name.replace("/", "__"), 0) + 1
        self._versions[name.replace("/", "__")] = version
        (self.versions_dir / f"{name.replace('/', '__')}@{version}").write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        if self._on_write:
            self._on_write(name, version, sha, len(data), agent)
        return {"path": name, "version": version, "bytes": len(data), "sha256": sha[:12]}
