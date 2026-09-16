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

## Free providers (checked 2026-09-16)

| Preset | Limits Cadre starts from | Source |
|---|---|---|
| `groq` | `openai/gpt-oss-120b`, `gpt-oss-20b`: 30 RPM, 1,000 RPD, 8,000 TPM, 200k TPD | provider docs |
| `gemini` | `gemini-2.5-flash`: 10 RPM, 250 RPD (conservative; real limits are shown only in AI Studio) | guess |
| `openrouter` | `:free` models, discovered: 20 RPM, 50/day (1,000/day once $10 credit was ever bought) | provider docs |
| `mistral` | medium/small: 2 RPM; free plan requires opting in to training on your data | reported |
| `cohere` | `command-a-03-2025`: 20 RPM, ~1,000 calls/month, non-commercial | reported |
| `nvidia` | `meta/llama-3.3-70b-instruct`: 40 RPM | reported |
| `cloudflare` | Llama 3.3 70B, gpt-oss-120b: 10,000 neurons/day; needs `--param account_id=…` | reported |
| `zai` | `glm-4.5-flash` | guess |
| `huggingface` | ~$0.10 credit/month | reported |
| `ollama`, `llamacpp`, `lmstudio` | local, no key, no limits — slow on an 8 GB laptop | — |
| `openai`, `anthropic`, `deepseek`, `custom` | paid, same adapter | — |

Free catalogues change without notice: Cerebras became a card-required trial and GitHub Models
was retired in July 2026. These numbers are only starting values — Cadre overrides them with the
provider's own rate-limit headers, and every one can be changed with `cadre provider set-model`.

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
