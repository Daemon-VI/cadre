# Cadre

**Cadre runs a team of AI agents that builds a new project or finishes an existing one, using
only free model API keys.** It spreads the work across every provider you add, stays inside each
one's rate limits, and when a day's quota runs out it parks the run and picks it up after the reset.

You describe the team as one YAML file: who the agents are, which tools they may use, which
checks decide "done", and how they work together. Reviewers are routed to a *different* model
family from the builder. Programs such as tests and linters decide whether work passes, not model
opinions, and votes are counted by code.

## Quick start

Cadre needs Python 3.12 or newer. Install it with `pipx install cadre-ai` or
`uv tool install cadre-ai`, or run it without installing through
[uv](https://docs.astral.sh/uv/getting-started/installation/) as `uvx --from cadre-ai cadre …`.
The command is `cadre`.

```bash
cadre provider add groq             # 1. a free key from console.groq.com, typed at a hidden prompt
cadre forecast project-finisher "Make the failing tests pass"                  # 2. will it fit today?
cadre run project-finisher "Make the failing tests pass" --project ./my-repo --allow-exec   # 3.
git -C my-repo log --stat cadre/<run-id>     # 4. review the branch; Cadre never merges
```

With no key at all, `cadre run decision-board "Should we open a second office?" --demo` runs an
offline scripted team.

## Measured, not claimed

Every number here was observed on free Groq and Google AI Studio keys on 2026-09-17/18 and is
recorded with its run id in [`docs/PROJECT_STATE.md`](https://github.com/Daemon-VI/cadre/blob/main/docs/PROJECT_STATE.md) (the M5 and M11
tables).

| What | Result |
|---|---|
| `decision-board` (a five-member council votes, then a memo is written) | succeeded · 15 calls · 21.5k tokens · 125 s |
| `software-team --allow-exec` (spec → build → independent review → tests gate) | succeeded · 27 calls · 94.0k tokens · 466 s |
| `project-finisher` on a half-built fixture repository | succeeded · 14 calls · 16.8k tokens · 123 s · owner's tree unchanged |
| Capstone 1: build a unit-converter CLI | succeeded · `Ran 9 tests … OK` when re-run by hand |
| Capstone 2: finish a half-built package | succeeded · `Ran 8 tests … OK` on the delivered branch; owner's HEAD, branch and working tree unchanged |
| Defects that only real models exposed | 10, each fixed with a regression test built from the live response |
| One-line change: rewrite the file vs `edit_file` | 7,056 vs 134 tokens (Cadre's 4-characters-a-token estimate) |
| Server memory while a live run streamed | 64.5 MB idle, 72.9 MB peak |

## Limitations

- **Checks run as you unless you contain them.** By default a check runs code the agents wrote,
  as you, on your machine. The model can only name a check declared in the org file, never supply
  a command, and checks need `--allow-exec` or your approval. A check can instead run in a
  container with no network (`runner: docker` or `podman`), but a container shares the host's
  kernel and the workspace stays writable: see ADR-031 for what that does not protect. Don't give
  an untrusted goal to an org whose checks run as you, or, in project mode, to one whose checks
  run in a container.
- **No run has parked live yet.** Parking on a daily limit and resuming after the reset is
  verified with a fake clock. A real interrupted run did resume the next day without re-billing.
- **Review quality is not measured.** Only the routing of reviewers to another model family is.
- **Forecasts are rough.** They were off by up to 4.4× before recalibration, and 2.3× on a later
  run, because history is kept per org and not per project size.
- **Tested by hand on Windows only.** CI covers Linux and macOS.
- **Free tiers change without notice.** Preset limits are dated starting values, corrected at
  run time by each provider's rate-limit headers.

## Install from source

Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Daemon-VI/cadre && cd cadre
uv sync
uv run cadre init          # creates ~/.cadre (set CADRE_HOME to put it elsewhere)
```

## Try it with no key

The demo mode swaps in a scripted, offline model that exercises every workflow end to end. It
is plumbing, not intelligence: every answer it writes is marked `[demo]`.

```bash
uv run cadre run decision-board "Should we open a second office?" --demo
uv run cadre run software-team "a word counter" --demo --allow-exec
```

## Front ends

Cadre is one engine (`cadre serve`, a local API at `/api/v1`), and each of these is a thin client
of it (ADR-024):

- **AI editors (MCP):** `uvx --from "cadre-ai[mcp]" cadre mcp` gives Claude Code, VS Code, Cursor,
  Windsurf and Antigravity five tools: forecast, start a run, run status, usage and list orgs.
  They can't approve anything. There's a config snippet for each host in
  [`docs/MCP_HOSTS.md`](https://github.com/Daemon-VI/cadre/blob/main/docs/MCP_HOSTS.md).
- **GitHub Action:** label an issue `cadre` or comment `/cadre <goal>`. Cadre runs
  `project-finisher` on the checkout and opens a pull request with its report and usage table.
  Only the owner, members and collaborators can trigger it. Copy
  [`examples/github-action/cadre.yml`](https://github.com/Daemon-VI/cadre/blob/main/examples/github-action/cadre.yml) and add a free key as a
  repository secret.
- **VS Code extension** (not published yet; it will go to the Marketplace and Open VSX, so also
  Antigravity, Cursor and Windsurf): build it from
  [`editors/vscode`](https://github.com/Daemon-VI/cadre/tree/main/editors/vscode).
- **Container:** `ghcr.io/daemon-vi/cadre`, non-root, state in `/data`, keys from environment
  variables. See [`compose.yaml`](compose.yaml).
- **Standalone downloads** for Windows, macOS and Linux are attached to each GitHub Release. They
  are unsigned, so SmartScreen and Gatekeeper will warn. Checks that use `{python}` need a Python
  on PATH.

On a server, in CI or in a container with no keychain, set `CADRE_NO_KEYRING=1`, put keys in
environment variables, and run `cadre provider add-from-env` to register every free provider
whose key is present.

## Add a free model

```bash
uv run cadre presets               # every provider Cadre knows, with free-tier notes
uv run cadre provider add groq     # prompts for the key with input hidden
uv run cadre provider list
uv run cadre provider test groq
uv run cadre provider key groq     # replace a stored key, e.g. after rotating it
```

The key goes to the OS credential store (Windows Credential Manager, macOS Keychain, Secret
Service), never to a file. Environment variables work too, checked first:
`CADRE_KEY_GROQ`, then the provider's usual name (`GROQ_API_KEY`). On a headless machine with no
keychain (a server, a container, CI), set `CADRE_NO_KEYRING=1` and pass keys only as environment
variables.

Add as many providers as you have keys. More providers means more parallel capacity and real
reviewer independence.

```bash
uv run cadre provider add gemini
uv run cadre provider add openrouter          # discovers the current :free models
uv run cadre provider models openrouter       # what the endpoint serves today
uv run cadre provider set-model groq openai/gpt-oss-20b --tier fast --tpm 8000
```

## Run a team

```bash
uv run cadre run software-team "a CSV to Markdown table converter" --allow-exec
uv run cadre run startup-company "Launch a flashcard app for engineering students"
uv run cadre runs
uv run cadre show <run-id> --events
uv run cadre resume <run-id>        # finished steps are reused, not re-billed
uv run cadre cancel <run-id>
```

Without `--allow-exec`, the first check asks you before running anything. Approval gates and
agent questions are asked in the terminal; add `--yes` to auto-approve gates, or
`--approve-elsewhere` to answer from the dashboard or `cadre approve <id>`.

Every run gets a workspace at `~/.cadre/runs/<run-id>/workspace/`, with every file version kept
under `versions/`.

## Finish an existing project

```bash
uv run cadre run project-finisher "Make the failing tests pass" --project C:\path\to\repo --allow-exec
```

Cadre works in a git worktree on a new branch `cadre/<run-id>` and commits each finished step
there with your repository's own identity. Your working tree, current branch and uncommitted
changes are never touched (a dirty tree is refused unless you pass `--allow-dirty`), and Cadre
never merges or pushes. Checks come from the org file and from `.cadre/checks.yaml` as committed
at the base commit, for example:

```yaml
checks:
  - name: tests
    command: ["{python}", "-m", "pytest", "-q"]
```

or, to run them in a container with no network (`{python}` is then the image's `python3`):

```yaml
checks:
  - name: tests
    command: ["{python}", "-m", "unittest", "-v"]
    runner: docker                     # or podman
    image: "python:3.12-slim@sha256:2f17fc044b579bab302c2e8054d3a686e2cb9a83de48e70534b94cd8ebbe06a9"
    memory: 512m                       # defaults: 512m, 1 CPU, 256 processes
    env: [LANG]                        # passed by name; key-like names are refused
```

Cadre never pulls the image: the first run tells you the `docker pull` command to run.

The run ends by printing the branch's commits, `git diff --stat`, and the commands to review or
discard it. `cadre runs cleanup` removes finished worktrees and keeps their branches.

## Will it fit? Usage and forecasts

```bash
uv run cadre forecast software-team "a CSV to Markdown table converter"
uv run cadre usage --days 7
```

The forecast answers *fits now*, *fits today after ~N min of waits*, *needs ~N days* or *cannot
run*, and always says its basis: `measured, n = …` once an org has finished runs, or `no history,
estimated from template size` before that. `cadre run` prints it before starting. The usage
ledger shows requests, tokens and the share of each daily cap per day, provider and model, with
the next reset in IST.

## Long jobs: parking and resuming

When every model a run can use has hit its *daily* limit, the run is **parked** instead of
failing, with the time it can continue (each provider's own reset clock). Nothing already finished
is repeated or billed again when it resumes.

```bash
uv run cadre serve                 # resumes parked runs by itself while it is running
uv run cadre resume --due          # or resume every run whose reset has passed, once
uv run cadre scheduler install     # or check every 30 minutes from the OS scheduler (asks first)
```

Budgets count everything a run has used across days. `max_days` (default 7) stops a run that has
gone on too long; `max_tokens_per_day` spreads a job over several days; `cadre resume <id>
--add-calls 20` raises a stopped run's allowance.

## Dashboard

```bash
uv run cadre serve       # http://127.0.0.1:8765, loopback only
uv run cadre ui          # opens the browser with the access token in the URL fragment
```

To reach it through a Tailscale name or from a container, add `--allowed-host NAME` (repeatable,
exact names only); anything else is still refused. The API is versioned at `/api/v1`.

Runs with a live timeline, files, per-agent token usage and results; organisations with a YAML
editor and validator; models and keys with live quota bars; pending approvals.

## Templates

| Template | Pattern | What it does |
|---|---|---|
| `software-team` | sequence → review loop → docs | PM writes a spec, engineer builds and tests, an independent reviewer checks it, `compile` and `tests` checks gate it, a writer documents it |
| `decision-board` | council | CFO, CTO, CMO and a risk officer propose, critique, and vote; the CEO chairs, breaks ties and writes the memo |
| `startup-company` | manager | A founder plans a task graph for researcher, engineer, designer and marketer; a QA lead reviews each deliverable |
| `project-finisher` | manager (repo checks, reviewer) | Finishes work in an existing repository (`--project`): the lead plans, the engineer edits, the repo's own checks gate, a reviewer on another family advises, a branch is delivered |
| `research-desk` | parallel → review loop → approval | Three analysts in parallel, an editor merges, a fact-checker reviews, a human approves |

Copy one to edit: `uv run cadre org new my-team --from software-team`, then
`uv run cadre org validate my-team`.

## The organisation file

```yaml
name: tiny-team
agents:
  - id: builder
    role: Python engineer
    tools: [list_files, read_file, write_file, run_check]
  - id: reviewer
    role: Code reviewer
    diverse_from: builder          # prefer a different model family
    tools: [list_files, read_file]
checks:
  - name: tests
    command: ["{python}", "-m", "unittest", "discover", "-p", "test_*.py"]
budget: {max_calls: 40, max_tokens: 150000, max_minutes: 20, max_parallel: 2}
workflow:
  - builder: builder
    reviewers: [reviewer]
    checks: [tests]
    max_rounds: 3
    task: "Build {goal} in app.py with tests in test_app.py."
```

Step types (`type:` may be omitted when the keys make it obvious):

| Type | Does |
|---|---|
| `agent` | one agent, one task, bounded tool turns |
| `sequence` | steps in order (a bare list is a sequence); `{prev}` is the last output |
| `parallel` | steps at once, optionally combined by a `join` agent |
| `review_loop` | builder → checks → reviewers (JSON verdicts) → feedback, up to `max_rounds` |
| `council` | proposals → critique rounds → options → votes → tally (`majority`, `supermajority`, `unanimous`, `plurality`) → memo |
| `manager` | manager plans a task graph, workers run it in dependency order, optional reviewer, final report |
| `approval` | wait for a human |

Templates can use `{goal}`, `{prev}` and `{out.<step id>}`. Tools: `list_files`, `read_file`,
`search`, `write_file`, `edit_file`, `post_note`, `run_check`, `ask_human` (see *Agent tools*).
Everything is validated before the first
model call, and errors name their path in the file.

## Free providers (checked 2026-09-17)

Every free chat model a key reaches is its own quota bucket. Each provider also has its own
daily clock and a recorded answer to "does the free tier train on my prompts?".

| Preset | Models and limits Cadre starts from | Day resets | Trains on free prompts |
|---|---|---|---|
| `groq` | `openai/gpt-oss-120b`, `gpt-oss-20b`, `qwen/qwen3.8-27b`: 30 RPM, 1,000 RPD, 8,000 TPM, 200k TPD each (docs). Two model families on one key | rolling (no clock published; headers override) | no |
| `gemini` | Seven chat models, each its own bucket: Gemini 3.8/3.7/3.6/3.5 Flash, 3 Flash preview, 3.5/3.1 Flash-Lite (the 2.5 models answered 404 to a new key on 2026-09-17). Google publishes no per-model numbers; Flash 20 RPD and Flash-Lite 500 RPD are third-party reports, the rest are guesses; a daily-quota 429 teaches Cadre the real limit | midnight Pacific (docs) | **yes** |
| `openrouter` | `:free` models, discovered: 20 RPM, 50/day (1,000/day once 10 credits were ever bought) (docs) | UTC day (docs) | unknown — depends on the model's host |
| `mistral` | medium/small: 2 RPM (reported) | not stated | **yes** unless you opt out |
| `cohere` | `command-a-03-2025`: 20 RPM, 1,000 calls/month, non-commercial (docs) | not stated | yes unless you opt out |
| `nvidia` | `meta/llama-3.3-70b-instruct`: 40 RPM (reported); trial credits | not stated | **yes** (trial terms) |
| `cloudflare` | Llama 3.3 70B, gpt-oss-120b: 10,000 neurons/day, 300 RPM; needs `--param account_id=…` | 00:00 UTC (docs) | no |
| `zai` | `glm-4.7-flash`, `glm-4.5-flash` (free per docs; limits guessed) | not stated | unknown |
| `huggingface` | ~$0.10 credit/month | counted per UTC day (the credit itself is monthly) | unknown — depends on the upstream provider |
| `ollama`, `llamacpp`, `lmstudio` | local, no key, no limits — slow on an 8 GB laptop | — | no |
| `deepseek`, `openai`, `anthropic`, `custom` | **paid**, opt-in, same adapter. DeepSeek has no free tier, and OpenRouter listed no free DeepSeek model on 2026-09-17 | — | — |

Free catalogues change without notice: Cerebras became a card-required trial and GitHub Models
was retired in July 2026. These numbers are only starting values. Cadre overrides them with each
provider's own rate-limit headers, `cadre provider refresh` shows what each endpoint serves today
(`--apply` adds new models and disables vanished ones, never touching limits you set), and any
number can be changed with `cadre provider set-model`. Cadre keeps 10% of every daily cap unused
(`reserve_pct`) for other programs that share the key.

Use **one account per provider.** Capacity grows by adding *different* providers; several
accounts or projects at one provider to multiply a free limit breaks their terms.

### Private runs

`cadre run … --private` (or `privacy: private` in the org file) only uses providers that say they
do not train on prompts — today Groq, Cloudflare and local models. The run records which models it
excluded, and fails before its first call if none are left.

## Agent tools

`list_files`, `read_file` (optionally a line range), `search` (regex, capped), `write_file`,
`edit_file` (replace one exact, unique match — far cheaper than rewriting a file), `post_note`,
`run_check` (a check *named* in the org file) and `ask_human`. An agent only gets the tools its
entry lists. Agents with file tools also see a capped repo map: paths, line counts and top-level
Python classes and functions.

## Safety

- **By default, checks run code the agents wrote, as you, on your machine. That is not a
  sandbox.** The model can only name a check declared in the org file; it never supplies a
  command. Checks run in the run's workspace with a timeout, capped output, and an environment
  stripped of anything that looks like a key, token or password. They need `--allow-exec` or
  your approval.
- **A check can run in a container instead** (`runner: docker` or `runner: podman`, with an
  image pinned by digest): the workspace is its only mount, with no network, a read-only root
  filesystem, no capabilities and capped processes, memory and CPU. Cadre never pulls the image.
  `--allow-container-exec` lets such checks run without asking while checks that run as you
  still ask. It does not protect against a kernel exploit, and the check can still change anything
  in the workspace — including, in project mode, the worktree's `.git` file, which Cadre's own git
  now refuses to trust (ADR-031).
- File tools cannot leave the run workspace (absolute paths, `..`, symlinks, drive letters,
  device names and alternate data streams are refused).
- The API listens on 127.0.0.1, requires a per-user bearer token on every call, rejects
  non-loopback `Host` headers, sends no CORS headers, and serves a strict CSP.
- **Users and roles (M13).** More than one person can share a server. Each has a token and a
  role — **viewer** (read), **member** (read, run, decide approvals) or **admin** (everything,
  plus managing users, tokens and providers). Manage them with `cadre user …` and `cadre token …`;
  tokens are stored hashed and shown once. Your existing `~/.cadre/token` is the bootstrap admin,
  so a single-user install is unchanged. An append-only audit log records who did what
  (`cadre audit`). Shared provider keys stay server-side: members use them without seeing them.
- **Teams (M13).** Group users into teams (`cadre team …`). A run belongs to a team, which can
  have a **budget** (runs and tokens per day, concurrent runs — checked when a run starts) and a
  **model allowance** (limit the team to certain providers/models). Cancelling or resuming a run
  and deciding its approvals is limited to an admin, the run's owner, or a member of its team.
  OIDC SSO is not built yet; per-user tokens are the only sign-in.
- Keys live in the OS credential store or your environment — never in `config.yaml`, the
  database, events, API responses or logs. Loaded keys are redacted from everything stored.
- Respect each provider's terms. Cadre spreads work across *different* providers; it does not
  create accounts or rotate several keys of one provider to get around a limit.

## Development

```bash
uv run pytest -q
uv run ruff check src tests
```

The tests need no network and no key: HTTP is mocked and models are scripted. See
[`CONTRIBUTING.md`](https://github.com/Daemon-VI/cadre/blob/main/CONTRIBUTING.md); report security problems privately as described in
[`SECURITY.md`](https://github.com/Daemon-VI/cadre/blob/main/SECURITY.md).

## Licence

Apache-2.0. See [`LICENSE`](https://github.com/Daemon-VI/cadre/blob/main/LICENSE).

## Documentation

- [`docs/SRS.md`](https://github.com/Daemon-VI/cadre/blob/main/docs/SRS.md) — requirements (the refined idea, FR/NFR list)
- [`docs/ARCHITECTURE.md`](https://github.com/Daemon-VI/cadre/blob/main/docs/ARCHITECTURE.md) — design and decision records
- [`docs/OBJECTIVES.md`](https://github.com/Daemon-VI/cadre/blob/main/docs/OBJECTIVES.md) — objectives, how each is met, evidence
- [`docs/TEST_PLAN.md`](https://github.com/Daemon-VI/cadre/blob/main/docs/TEST_PLAN.md) — test strategy and requirement traceability
- [`docs/ROADMAP.md`](https://github.com/Daemon-VI/cadre/blob/main/docs/ROADMAP.md) — what comes next, and what was deferred and why
- [`docs/PROJECT_STATE.md`](https://github.com/Daemon-VI/cadre/blob/main/docs/PROJECT_STATE.md) — where the project actually stands
