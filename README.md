# Cadre

Run an organisation of AI agents — builders, reviewers, verifiers, deciders and managers — on
the free model APIs you already have.

Cadre is a self-hosted platform. You declare an organisation as one YAML file (who the agents
are, what tools they may use, which checks decide "done", and how they work together), add
whichever free model keys you have, and give it a goal. Cadre schedules every model call so the
team stays inside each key's free limits, routes reviewers to a *different* model family from
the builder, lets programs (tests, linters) — not model opinions — decide whether work passes,
and counts votes in code.

Three things drive the design (see `docs/SRS.md` §1):

1. **On free tiers, rate limits bind, not money.** Groq's free `openai/gpt-oss-120b` allows
   8,000 tokens a minute; OpenRouter's `:free` models allow 50 requests a day. Cadre is first a
   quota-aware scheduler across every key you add.
2. **Review is only worth something if it is independent**, and a failing check always beats an
   approving reviewer.
3. **Group decisions must be auditable**: strict-JSON votes, tallied by code, dissent kept.

## Install

Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cd cadre
uv sync
uv run cadre init          # creates ~/.cadre (set CADRE_HOME to put it elsewhere)
```

## Try it with no key

The demo mode swaps in a scripted, offline model that exercises every workflow end to end. It
is plumbing, not intelligence — every answer it writes is marked `[demo]`.

```bash
uv run cadre run decision-board "Should we open a second office?" --demo
uv run cadre run software-team "a word counter" --demo --allow-exec
```

## Add a free model

```bash
uv run cadre presets               # every provider Cadre knows, with free-tier notes
uv run cadre provider add groq     # prompts for the key with input hidden
uv run cadre provider list
uv run cadre provider test groq
```

The key goes to the OS credential store (Windows Credential Manager, macOS Keychain, Secret
Service), never to a file. Environment variables work too, checked first:
`CADRE_KEY_GROQ`, then the provider's usual name (`GROQ_API_KEY`).

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

## Dashboard

```bash
uv run cadre serve       # http://127.0.0.1:8765, loopback only
uv run cadre ui          # opens the browser with the access token in the URL fragment
```

Runs with a live timeline, files, per-agent token usage and results; organisations with a YAML
editor and validator; models and keys with live quota bars; pending approvals.

## Templates

| Template | Pattern | What it does |
|---|---|---|
| `software-team` | sequence → review loop → docs | PM writes a spec, engineer builds and tests, an independent reviewer checks it, `compile` and `tests` checks gate it, a writer documents it |
| `decision-board` | council | CFO, CTO, CMO and a risk officer propose, critique, and vote; the CEO chairs, breaks ties and writes the memo |
| `startup-company` | manager | A founder plans a task graph for researcher, engineer, designer and marketer; a QA lead reviews each deliverable |
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
`write_file`, `post_note`, `run_check`, `ask_human`. Everything is validated before the first
model call, and errors name their path in the file.

## Free providers (checked 2026-09-17)

Every free chat model a key reaches is its own quota bucket. Each provider also has its own
daily clock and a recorded answer to "does the free tier train on my prompts?".

| Preset | Models and limits Cadre starts from | Day resets | Trains on free prompts |
|---|---|---|---|
| `groq` | `openai/gpt-oss-120b`, `gpt-oss-20b`, `qwen/qwen3.8-27b`: 30 RPM, 1,000 RPD, 8,000 TPM, 200k TPD each (docs). Two model families on one key | rolling (no clock published; headers override) | no |
| `gemini` | Ten chat models, each its own bucket: Gemini 3.8/3.7/3.6/3.5 Flash and 3 Flash preview, 2.5 Flash and 2.5 Pro, 3.5/3.1/2.5 Flash-Lite. Google publishes no per-model numbers; Flash 20 RPD and Flash-Lite 500 RPD are third-party reports, the rest are guesses | midnight Pacific (docs) | **yes** |
| `openrouter` | `:free` models, discovered: 20 RPM, 50/day (1,000/day once 10 credits were ever bought) (docs) | UTC day (docs) | unknown — depends on the model's host |
| `mistral` | medium/small: 2 RPM (reported) | not stated | **yes** unless you opt out |
| `cohere` | `command-a-03-2025`: 20 RPM, 1,000 calls/month, non-commercial (docs) | not stated | yes unless you opt out |
| `nvidia` | `meta/llama-3.3-70b-instruct`: 40 RPM (reported); trial credits | not stated | **yes** (trial terms) |
| `cloudflare` | Llama 3.3 70B, gpt-oss-120b: 10,000 neurons/day, 300 RPM; needs `--param account_id=…` | 00:00 UTC (docs) | no |
| `zai` | `glm-4.7-flash`, `glm-4.5-flash` (free per docs; limits guessed) | not stated | unknown |
| `huggingface` | ~$0.10 credit/month | monthly | unknown — depends on the upstream provider |
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

- **Checks run code the agents wrote, as you, on your machine. This is not a sandbox.** The
  model can only name a check declared in the org file; it never supplies a command. Checks run
  in the run's workspace with a timeout, capped output, and an environment stripped of anything
  that looks like a key, token or password. They need `--allow-exec` or your approval. Don't
  give an untrusted goal to an org with checks; a container runner is on the roadmap.
- File tools cannot leave the run workspace (absolute paths, `..`, symlinks, drive letters,
  device names and alternate data streams are refused).
- The API listens on 127.0.0.1, requires a bearer token (`~/.cadre/token`) on every call,
  rejects non-loopback `Host` headers, sends no CORS headers, and serves a strict CSP.
- Keys live in the OS credential store or your environment — never in `config.yaml`, the
  database, events, API responses or logs. Loaded keys are redacted from everything stored.
- Respect each provider's terms. Cadre spreads work across *different* providers; it does not
  create accounts or rotate several keys of one provider to get around a limit.

## Development

```bash
uv run pytest -q
uv run ruff check src tests
```

The tests need no network and no key: HTTP is mocked and models are scripted.

## Documentation

- [`docs/SRS.md`](docs/SRS.md) — requirements (the refined idea, FR/NFR list)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — design and decision records
- [`docs/OBJECTIVES.md`](docs/OBJECTIVES.md) — objectives, how each is met, evidence
- [`docs/TEST_PLAN.md`](docs/TEST_PLAN.md) — test strategy and requirement traceability
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — what comes next, and what was deferred and why
- [`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) — where the project actually stands
