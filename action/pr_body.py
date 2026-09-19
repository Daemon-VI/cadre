"""Turn `cadre run --result-json` output into step outputs, a pull-request body, or an issue
comment for the GitHub Action (FR-18). Standard library only; runs with the runner's python3.

    pr_body.py outputs|body|comment RESULT.json
    GOAL=... pr_body.py title
"""

from __future__ import annotations

import json
import os
import sys

MAX_REPORT = 60_000  # GitHub caps a PR body and a comment at 65,536 characters


def usage_table(r: dict) -> str:
    rows = ["| Agent | Model | Calls | Tokens in | Tokens out |", "|---|---|---|---|---|"]
    for u in r.get("usage", []):
        rows.append(f"| {u['agent']} | {u['provider']}/{u['model']} | {u['calls']} | "
                    f"{u['prompt_tokens']:,} | {u['completion_tokens']:,} |")
    t = r.get("totals", {})
    rows.append(f"| **total** | | **{t.get('calls', 0)}** | **{t.get('prompt_tokens', 0):,}** | "
                f"**{t.get('completion_tokens', 0):,}** |")
    return "\n".join(rows)


def report_text(r: dict, limit: int = MAX_REPORT) -> str:
    report = (r.get("result") or "_The run returned no report._").strip()
    if len(report) > limit:
        report = report[:limit] + "\n\n…(cut; the full report is in the workflow run's artifact)"
    return report


def body(r: dict, issue: str = "") -> str:
    report = report_text(r)
    commits = "\n".join(f"- `{c}`" for c in r.get("commits", [])[:30]) or "- (none)"
    status = r["status"] + (" — a gate or check did not approve; review with extra care"
                            if r["status"] == "unapproved" else "")
    return (f"{report}\n\n---\n\n**Goal:** {r.get('goal')}  \n**Org:** `{r.get('org')}` · "
            f"**Run:** `{r['id']}` · **Status:** {status}\n\n**Commits**\n{commits}\n\n"
            f"```\n{r.get('diff_stat') or ''}\n```\n\n**Usage (free keys)**\n\n{usage_table(r)}\n\n"
            "_Opened by the Cadre action. Nothing is merged automatically: review this like any "
            "other pull request._\n" + (f"\nCloses #{issue}\n" if issue.isdigit() else ""))


def comment(r: dict, pr_url: str = "") -> str:
    s = r["status"]
    if s == "parked":
        return (f"Cadre run `{r['id']}` is **parked**: every model it can use hit a daily free-tier "
                f"limit. It could continue after about **{r.get('resume_at_ist')}**. This action "
                "does not resume across jobs yet; re-run it after that time.")
    if pr_url:
        return f"Cadre run `{r['id']}` finished ({s}): {pr_url} has the report and usage."
    if s in ("succeeded", "unapproved"):
        head = (f"Cadre run `{r['id']}` finished ({s}) with {len(r.get('commits', []))} commit(s), "
                "so there is **no pull request**.")
    else:
        head = f"Cadre run `{r['id']}` ended **{s}**: {r.get('error') or 'see the workflow log'}"
    return (f"{head} The timeline is in the workflow log (group \"Cadre timeline\").\n\n"
            f"<details><summary>Report</summary>\n\n{report_text(r, 50_000)}\n\n</details>\n\n"
            f"**Usage (free keys)**\n\n{usage_table(r)}\n")


def outputs(r: dict) -> str:
    return "\n".join([f"run-id={r['id']}", f"status={r['status']}", f"branch={r.get('branch') or ''}",
                      f"commits={len(r.get('commits', []))}"])


def title(goal: str, limit: int = 72) -> str:
    """The pull request's title: the goal's first line (an issue's title), cut at a word."""
    first = " ".join(next((ln for ln in goal.splitlines() if ln.strip()), "").split())
    if len(first) > limit:
        cut = first[:limit]
        first = (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(".,;:") + "…"
    return "Cadre: " + (first or "work from an issue")


def main() -> int:
    if sys.argv[1] == "title":
        print(title(os.environ.get("GOAL", "")))
        return 0
    mode, path = sys.argv[1], sys.argv[2]
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    if mode == "comment":
        print(comment(r, os.environ.get("PR_URL", "")))
    else:
        print(body(r, os.environ.get("ISSUE", "")) if mode == "body" else outputs(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
