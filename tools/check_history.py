"""History and privacy checks, run in CI (FR-14.3, FR-14.4). Exit status 1 on any finding.

  * every commit is authored by the owner's noreply identity (merges made in GitHub's web UI may
    have GitHub as committer);
  * the laptop account's user name appears in no tracked file, path or commit metadata;
  * no full-length Groq / Google / OpenRouter key shape appears anywhere in the history.
Findings name commits and paths, never the matched key text.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

OWNER = "Rithik Krishna <317035893+Daemon-VI@users.noreply.github.com>"
WEB_COMMITTER = "GitHub <noreply@github.com>"
# The laptop account's user name, written so that this file never matches itself.
PRIVATE = re.compile(r"m[p]ps[\s_-]*kana[j]iguda", re.IGNORECASE)
KEYS = re.compile(r"gsk_[A-Za-z0-9]{40,}|AIza[0-9A-Za-z_-]{35}|sk-or-v1-[0-9a-f]{64}")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, check=True, encoding="utf-8",
                          errors="replace").stdout


def wrong_identities() -> list[str]:
    bad = []
    for line in git("log", "--all", "--format=%H|%an <%ae>|%cn <%ce>").splitlines():
        sha, author, committer = line.split("|")
        if author != OWNER or committer not in (OWNER, WEB_COMMITTER):
            bad.append(f"{sha[:10]}: author {author}, committer {committer}")
    return bad


def private_name_hits() -> list[str]:
    hits = []
    for path in git("ls-files", "-z").split("\0"):
        if not path:
            continue
        if PRIVATE.search(path):
            hits.append(path)
            continue
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if PRIVATE.search(text):
            hits.append(path)
    if PRIVATE.search(git("log", "--all", "--format=%an%n%ae%n%cn%n%ce%n%B")):
        hits.append("<commit metadata>")
    return hits


def key_shapes_in_history() -> list[str]:
    commits = []
    sha = ""
    for line in git("log", "--all", "-p", "--no-color", "--format=commit %H").splitlines():
        if line.startswith("commit "):
            sha = line[7:17]
        elif KEYS.search(line) and sha not in commits:
            commits.append(sha)
    return commits


def main() -> int:
    failed = False
    for title, found in (("commits not by the owner", wrong_identities()),
                         ("private user name found in", private_name_hits()),
                         ("key-shaped strings in commits", key_shapes_in_history())):
        if found:
            failed = True
            print(f"FAIL {title}:")
            for f in found:
                print(f"  {f}")
        else:
            print(f"ok   no {title}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
