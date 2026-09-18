# Changelog

## 0.1.0 — unreleased

First version (Cadre distribution D4, FR-19).

- Finds the local Cadre server on `cadre.port`; starts `cadre serve` or `uvx cadre-ai serve`,
  detached, when a view or command needs it.
- Runs view with status icons; run view with a live event timeline, result and files.
- Status bar: today's usage against the tightest daily limit, and waiting approvals.
- Gate and question approvals as notifications; permission to execute code as a modal that shows
  each check's exact command.
- Commands: Start run on this folder, Forecast, Review branch, Add provider, Open dashboard,
  Review pending approvals, Cancel run, Resume run, Start server.
- The API token is read from `CADRE_HOME/token` per request and never stored, shown, logged or
  passed to a webview.
