# Changelog

## 1.3.0 — 2026-09-20

First published version (Cadre distribution D4, FR-19). The extension's version tracks the
Cadre engine it was built against, so it starts at 1.3.0 rather than 0.1.0.

- The run view says where each check ran: as you, or in Docker or Podman with its image and no
  network (Cadre M12). The approval's modal lists the same for every check.

- Finds the local Cadre server on `cadre.port`; starts `cadre serve` or `uvx cadre-ai serve`,
  detached, when a view or command needs it.
- Runs view with status icons; run view with a live event timeline, result and files.
- Status bar: today's usage against the tightest daily limit, and waiting approvals.
- Gate and question approvals as notifications; permission to execute code as a notification whose
  Review… opens a modal that shows each check's exact command. A poll never opens the modal, so a
  key pressed in another window can't approve (found on screen, 2026-09-19).
- Commands: Start run on this folder, Forecast, Review branch, Add provider, Open dashboard,
  Review pending approvals, Cancel run, Resume run, Start server.
- The API token is read from `CADRE_HOME/token` per request and never stored, shown, logged or
  passed to a webview.
