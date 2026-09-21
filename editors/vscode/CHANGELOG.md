# Changelog

## 1.4.0 — 2026-09-21

Five panels, each an editor tab (Command Palette, or the Runs view's title bar):

- **New run…** — a single form: organisation, goal, folder (or none), offline demo model,
  private. **Forecast** before you start. Keeps a half-typed goal across tab switches.
- **Usage** — per-model meters against today's daily caps, stating their value and state in
  words, plus a 1/7/14/30-day ledger with totals. Refreshes itself while visible.
- **Approvals** — everything waiting, oldest first; **Decide…** opens the same dialog as a
  notification, so nothing is approved from the page.
- **Memory** — remembered facts by scope: add, delete, and decide a model's proposals. A proposal
  whose approval has closed (for example after the database was reset) is marked and can be
  deleted instead of being stuck.
- **Organisations** — agents, roles, tools, checks and budget for yours and the templates.
  **Edit YAML** opens your own org's file; templates open read-only. **New run with this org**.

Also: a *private* option for runs and forecasts started from the extension, and memory
proposals are labelled as such in notifications. Every panel follows the run view's rules — no
token in any webview, text only via `textContent`, a fixed list of messages checked in the
extension, a folder chosen by index rather than by path, and no way to approve from a page.

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
