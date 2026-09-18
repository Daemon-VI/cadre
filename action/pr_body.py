"""Turn `cadre run --result-json` output into step outputs, a pull-request body, or an issue
comment for the GitHub Action (FR-18). Standard library only; runs with the runner's python3.

    pr_body.py outputs|body|comment RESULT.json
"""

from __future__ import annotations

import json
import sys

MAX_REPORT = 60_000  # GitHub caps a PR body at 65,536 characters


def usage_table(r: dict) -> str:
    rows = ["| Agent | Model | Calls | Tokens in | Tokens out |", "|---|---|---|---|---|"]
    for u in r.get("usage", []):
        rows.append(f"| {u['agent']} | {u['provider']}/{u['model']} | {u['calls']} | "
                    f"{u['prompt_tokens']:,} | {u['completion_tokens']:,} |")
    t = r.get("totals", {})
    rows.append(f"| **total** | | **{t.get('calls', 0)}** | **{t.get('prompt_tokens', 0):,}** | "
                f"**{t.get('completion_tokens', 0):,}** |")
    return "\n".join(rows)


def body(r: dict) -> str:
    report = (r.get("result") or "_The run returned no report._").strip()
    if len(report) > MAX_REPORT:
        report = report[:MAX_REPORT] + "\n\n…(cut; the full report is in the run's workspace)"
    commits = "\n".join(f"- `{c}`" for c in r.get("commits", [])[:30]) or "- (none)"
    status = r["status"] + (" — a gate or check did not approve; review with extra care"
                            if r["status"] == "unapproved" else "")
    return (f"{report}\n\n---\n\n**Goal:** {r.get('goal')}  \n**Org:** `{r.get('org')}` · "
            f"**Run:** `{r['id']}` · **Status:** {status}\n\n**Commits**\n{commits}\n\n"
            f"```\n{r.get('diff_stat') or ''}\n```\n\n**Usage (free keys)**\n\n{usage_table(r)}\n\n"
            "_Opened by the Cadre action. Nothing is merged automatically: review this like any "
            "other pull request._\n")


def comment(r: dict) -> str:
    s = r["status"]
    if s == "parked":
        return (f"Cadre run `{r['id']}` is **parked**: every model it can use hit a daily free-tier "
                f"limit. It could continue after about **{r.get('resume_at_ist')}**. This action "
                "does not resume across jobs yet; re-run it after that time.")
    if s in ("succeeded", "unapproved"):
        return f"Cadre run `{r['id']}` finished ({s}). See the pull request for the report and usage."
    return f"Cadre run `{r['id']}` ended **{s}**: {r.get('error') or 'see the workflow log'}"


def outputs(r: dict) -> str:
    return "\n".join([f"run-id={r['id']}", f"status={r['status']}", f"branch={r.get('branch') or ''}"])


def main() -> int:
    mode, path = sys.argv[1], sys.argv[2]
    with open(path, encoding="utf-8") as f:
        r = json.load(f)
    print({"outputs": outputs, "body": body, "comment": comment}[mode](r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
