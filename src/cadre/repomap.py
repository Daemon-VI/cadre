"""A capped map of the workspace for agents that work on files (FR-13, ADR-022).

One line per text file: its path, its line count and, for Python, the top-level `def` and
`class` names from `ast`. The whole block is cut at a token cap, because it is resent on every
call an agent makes. Parsed results are cached by (size, mtime) so a large repository is not
re-parsed on every turn.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .workspace import Workspace

DEFAULT_CAP_TOKENS = 1200
CHARS_PER_TOKEN = 4
_MAX_NAMES_CHARS = 240
_cache: dict[tuple[str, int, int], str] = {}


def _describe(ws: Workspace, path: str, size: int) -> str:
    target = ws.root / path
    try:
        stat = target.stat()
    except OSError:
        return f"{path} (unreadable)"
    key = (str(target), stat.st_size, stat.st_mtime_ns)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    data = target.read_bytes() if size <= 512_000 else b""
    if not data and size:
        line = f"{path} ({size} B, too large to map)"
    elif b"\x00" in data[:4096]:
        line = f"{path} (binary, {size} B)"
    else:
        text = data.decode("utf-8", errors="replace")
        count = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        line = f"{path} ({count} lines)"
        if path.endswith(".py"):
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                line += ": does not parse"
            else:
                # public classes first, then public functions: the names an agent navigates by
                classes = [f"class {n.name}" for n in tree.body
                           if isinstance(n, ast.ClassDef) and not n.name.startswith("_")]
                funcs = [f"def {n.name}" for n in tree.body
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and not n.name.startswith("_")]
                names = classes + funcs
                if names:
                    joined = ", ".join(names)
                    if len(joined) > _MAX_NAMES_CHARS:
                        joined = joined[:_MAX_NAMES_CHARS].rsplit(",", 1)[0] + ", …"
                    line += ": " + joined
    if len(_cache) > 5000:
        _cache.clear()
    _cache[key] = line
    return line


def repo_map(ws: Workspace, cap_tokens: int = DEFAULT_CAP_TOKENS) -> str:
    files = ws.files()
    if not files:
        return "(empty)"
    budget = cap_tokens * CHARS_PER_TOKEN
    out: list[str] = []
    used = 0
    for i, (path, size) in enumerate(files):
        line = _describe(ws, path, size)
        if used + len(line) + 1 > budget:
            out.append(f"… {len(files) - i} more files (use search or list_files)")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)
