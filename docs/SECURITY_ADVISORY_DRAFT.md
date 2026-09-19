# Security advisory (DRAFT — for Rithik to review and publish)

Create this under the repository's **Security → Advisories → New draft advisory** (or with
`gh api`), review, then publish. It is not published by writing this file.

---

- **Ecosystem:** PyPI
- **Package:** `cadre-ai`
- **Affected versions:** `>= 1.0.0, < 1.1.0` (i.e. 1.0.0 and 1.0.1)
- **Patched version:** `1.1.0`
- **Severity:** Low (CVSS v3.1 base 4.2 — suggested vector
  `AV:L/AC:H/PR:L/UI:N/S:U/C:N/I:H/A:N`; adjust after review)
- **CWE:** CWE-862 Missing Authorization
- **Title:** MCP `cadre_start_run` could skip the human `exec` approval, running model-written
  checks unapproved

## Summary

Cadre's MCP server exposes a `cadre_start_run` tool. In 1.0.0 and 1.0.1 that tool forwarded an
`allow_exec` argument to the run API, and `allow_exec` makes the engine skip the human `exec`
approval that otherwise gates running a check's (model-written) code. ADR-027 states that **no
approval of any kind is granted over MCP**, so this contradicted the documented and intended
behaviour: a model — or a prompt injection reaching the MCP client in the host's context — could
start a run whose declared checks executed on the machine with nobody approving.

## Impact

An attacker who can drive the MCP client (for example, prompt injection in the editor/agent
context that Cadre's MCP server serves) could set `allow_exec` when starting a run and have the
run's **declared** checks execute without the human `exec` approval. The checks are still only
those named in the org file (a model cannot supply an arbitrary command — ADR-006), the checks
run in the run's workspace with the usual timeout, output cap and key-scrubbed environment, and
they are **not** auto-approved for gate/question approvals. So the blast radius is "the org's own
declared checks run one step earlier than a human intended," not arbitrary command execution.

Real-world risk is reduced because the MCP server binds to a local stdio transport used by a
consenting host, and because an attacker still needs a run whose org declares a check.

## Affected configurations

- Running `cadre mcp` (the MCP server, from the `[mcp]` extra) and starting runs through the
  `cadre_start_run` tool, in versions 1.0.0 and 1.0.1.

## Patches

Fixed in **1.1.0**: `cadre_start_run` no longer accepts or forwards `allow_exec`; it always sets
`allow_exec: false` and `auto_approve: false`. Regression tests assert that no MCP tool schema
carries `allow_exec` and that a run started over MCP cannot skip the `exec` approval. Upgrade with
`pip install -U cadre-ai` (or `uv tool install cadre-ai@1.1.0`, `uvx cadre-ai@1.1.0`).

## Workarounds

If you cannot upgrade immediately: do not run the MCP server against an untrusted host/agent
context, and do not declare checks in orgs you start over MCP. Approving runs only from the
dashboard or `cadre approve` does not help here, because the bug skipped the approval entirely.

## Credit

Found in-house during work on the container runner (M12), by an automated claim-audit of the
security documentation.

## Timeline

- 2026-09-19: introduced in 1.0.0, shipped again in 1.0.1.
- 2026-09-19: found and fixed; 1.1.0 released with the fix.
