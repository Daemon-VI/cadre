# Cadre for VS Code

[Cadre](https://github.com/Daemon-VI/cadre) runs an organisation of AI agents — builders,
reviewers, verifiers, deciders, managers — declared in one YAML file, on whichever free model
APIs you have added. This extension puts Cadre inside the editor: start a run on the folder you
have open, watch it work, answer its approvals, and review the branch it wrote.

It is a thin client. All routing, quota arithmetic, runs and approvals live in the Cadre engine
(`cadre serve`, a local server on `127.0.0.1`); the extension talks to its API and re-implements
none of it. It is published to the VS Code Marketplace and to Open VSX, the registry Cursor,
Windsurf and Antigravity install from.

## What it does

- **Runs view** (the Cadre icon in the activity bar): recent runs with a status icon each —
  running, waiting for you, parked on a daily limit, succeeded, failed.
- **Run view**: a live timeline of the run's events (model calls, tool calls, checks, review
  rounds, votes), its result and the files it wrote. Updates as the run works.
- **Status bar**: today's usage against the tightest daily limit across your models — the largest
  of requests/RPD and tokens/TPD — and how many approvals are waiting.
- **Approvals**: a gate or an agent's question arrives as a notification (Approve / Reject, or
  Answer…). A request to **execute code** arrives as a notification with **Review…**, which opens a modal
  dialog; see Security below.
- **Commands** (Command Palette, category *Cadre*):
  - *Start run on this folder* — project mode on the workspace root: pick an organisation, type a
    goal. The run works on its own branch `cadre/<run-id>` in a git worktree; your working tree
    is not touched. Cadre refuses a folder with uncommitted changes and says why; you can then
    start from `HEAD` anyway.
  - *Forecast* — whether a goal fits in today's free quota, from measured history when there is
    some.
  - *Review branch* — the files a project run changed between its base commit and
    `cadre/<run-id>`, each opened as a diff.
  - *Add provider* — opens a terminal running `cadre provider add <id>`.
  - *Open dashboard* — the full web dashboard in your browser.
  - *Review pending approvals*, *Cancel run*, *Resume run*, *Start server*.

With no model key yet, a run can use Cadre's offline demo model, which exercises the whole
workflow (not real intelligence) so you can see how it behaves.

## Requirements

- **[uv](https://docs.astral.sh/uv/)** on `PATH` (the extension then runs `uvx cadre-ai serve`),
  **or** `cadre` itself on `PATH` (`uv tool install cadre-ai`, or `pip install cadre-ai`).
- `git`, for project mode and *Review branch*.
- At least one free model key for real work (Groq and Google AI Studio both give one without a
  card). Add it with *Cadre: Add provider*.

When a Cadre view or command needs the server and none answers on the configured port, the
extension starts `cadre serve` (or `uvx cadre-ai serve`) in the background, detached, so runs keep
going after the editor closes. Its output goes to `server.log` in the extension's global storage
folder. Nothing is started merely because the editor opened.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `cadre.port` | `8765` | Port of the local server. The extension only ever connects to `127.0.0.1`. |
| `cadre.autoStart` | `true` | Start the server when it is needed and not running. When off, the extension asks first. |

The server's home is `CADRE_HOME` (default `~/.cadre`). If you run Cadre with a different
`CADRE_HOME`, start VS Code with the same environment variable.

## Security

- **The API token.** Every API call needs the bearer token that the server keeps in
  `CADRE_HOME/token`. The extension reads that file at the moment of each request, inside the
  extension host, and keeps it nowhere: not in settings (which Settings Sync would upload), not
  in VS Code's SecretStorage, not in logs, not in any message or webview. It is never displayed.
- **Model keys** never pass through the extension. *Add provider* runs the Cadre CLI in the
  integrated terminal, where you type the key into its hidden prompt; the CLI stores it in your
  operating system's credential store.
- **Executing code.** A run's checks (tests, linters) execute code the agents wrote, as you, on
  this machine — it is not a sandbox. The extension never starts a run with execution
  pre-approved. When a run first wants to run its checks, a notification says so; it never takes
  keyboard focus, so nothing typed into another window can answer it. **Review…** opens a modal
  dialog that shows each check's name and its exact command from the organisation file; only an
  explicit **Allow execution** click approves. Closing the dialog decides nothing (the run keeps waiting); **Reject** rejects.
- **The run view** is a webview with a strict Content Security Policy: one script, identified by
  a per-load nonce, from the extension itself; no remote resources; no network access. All text
  an agent wrote is inserted as text (`textContent`), never parsed as HTML. The webview receives
  run data by message from the extension and never sees the token.
- **Notifications** that quote an agent (a question, a gate) have Markdown link syntax broken up,
  so model text cannot become a clickable command link.
- **Open dashboard** runs `cadre ui`, which opens the browser with the token in the URL
  *fragment* — the part after `#` that browsers never send to a server — and the dashboard
  removes it from the address bar at once. The extension itself never handles that URL. Without
  the CLI on `PATH` it opens the plain address and the dashboard asks for the token.
- **Untrusted folders.** In Restricted Mode the extension does not start runs on the folder or run
  `git` in it; watching runs and answering approvals still work.

## Troubleshooting

- *"the server rejected the token"* — the server is running with another `CADRE_HOME` than the one
  VS Code sees.
- *The server did not start* — open the server log from the error message. A port already in use
  is the usual cause; change `cadre.port`.
- *A provider added while the server was running is not used* — restart the server; it reads its
  providers when it starts.

## Licence

Apache-2.0. Source: [github.com/Daemon-VI/cadre](https://github.com/Daemon-VI/cadre), under
`editors/vscode`.
