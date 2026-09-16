# Cadre — Roadmap

_2026-09-16 · v0.1. Status with evidence is in `PROJECT_STATE.md`; decisions are in
`ARCHITECTURE.md` (ADR numbers below point there)._

## Order, and why this order

The order follows the SDLC and the one constraint that decides everything: a team of agents on
free keys fails by **rate limit** before it fails for any other reason. So the scheduler came
before the patterns, the patterns before the interfaces, and real-model measurement comes before
any new capability, because every capability costs tokens that have to fit the same free tiers.

| # | Milestone | Status | Why it sits here |
|---|---|---|---|
| **M0** | Requirements and design — `SRS.md`, `ARCHITECTURE.md` (ADR-001…015) | done 2026-09-16 | The raw idea ("free models, a company of agents") needed three decisions before any code: rate limits are the constraint, review must be independent, votes must be counted by code |
| **M1** | Providers, quota, router — one OpenAI-compatible adapter, 15 dated presets, RPM/TPM/RPD/TPD limiter, header learning, 429 cooldown, fallback, independence routing | done 2026-09-16 | Everything else calls models through it |
| **M2** | Org model, workspace, tools, store — YAML schema with reference validation, confined versioned workspace, six tools, named checks, SQLite | done 2026-09-16 | The patterns need somewhere to put files, events and results |
| **M3** | Collaboration patterns — agent, sequence, parallel, review loop, council, manager, approval; budgets; resume by step path | done 2026-09-16 | The actual product: building, reviewing, verifying, deciding, managing |
| **M4** | Interfaces — CLI, REST API with event stream, local dashboard, offline demo mode | done 2026-09-16 | Makes it usable by someone who is not reading the code |
| **M5** | **Live verification on a real free key** | **NEXT** | See below |
| M6 | Container runner for checks | planned | Checks run model-written code as the owner (ADR-006) |
| M7 | Multi-user organisations | planned | "Organisation scale" in the original idea |
| M8 | Memory across runs | planned | Teams repeat themselves between runs |
| M9 | Web research tool | planned | Research and decision orgs need sources |
| M10 | Hosted deployment | planned | Only after M6 and M7 make it safe to share |

## M5 — live verification (next)

**Blocked on Rithik adding a key** — Groq (`uv run cadre provider add groq`) and, ideally, Gemini
as a second family so independence routing has somewhere to go. Nothing about a real model's
behaviour is verified until this is done; `PROJECT_STATE.md` says so.

What to do and record:

1. Run each template once on real keys: `software-team` with `--allow-exec`, `decision-board`,
   `startup-company`, `research-desk --yes`.
2. Record per run: model calls, prompt/completion tokens, wall time, waits and fallbacks, final
   status, and whether reviews were independent. Put the numbers in `PROJECT_STATE.md`.
3. Confirm header learning: Groq's `x-ratelimit-remaining-tokens` / `reset-tokens` should appear
   in the limiter snapshot (`GET /api/quota`) and a burst should produce a `route.wait` event
   instead of a 429.
4. Confirm the JSON tool protocol fallback against at least one OpenRouter `:free` model that
   lacks function calling.
5. Measure the fixed prompt cost per call per template (system + roster + rules + tool schemas)
   and cut anything that does not earn its tokens.
6. Measure server RSS while a run streams to the dashboard (NFR-2 target: < 150 MB).

## M6 — container runner for checks

Checks currently run in a subprocess with a scrubbed environment, a timeout and capped output —
not a sandbox. Add an optional `runner: docker|podman` per check: workspace mounted read-write,
no network, CPU/memory caps, image chosen in the org file. Keep the subprocess runner as the
default, because Docker alongside a Gradle build does not fit this laptop's memory.

## M7 — multi-user organisations

Users and roles (owner, operator, viewer), per-team budgets and model allowances, approvals
routed to a role rather than to whoever is watching, an audit log of every approval and key
change, and SSO (OIDC) for a real organisation. Shared provider keys stay server-side; members
never see them.

## M8 — memory across runs

Per-organisation knowledge files (decisions taken, conventions, glossary) written by an explicit
step and injected into prompts with a hard size cap. Files first, not a vector database: every
token of memory is replayed on every call and counts against the same per-minute limits, so
memory has to earn its place (the same arithmetic Tessera applied to tools).

## M9 — web research tool

A `fetch_url` / `search` tool for research and decision orgs, with the result treated strictly as
data: fetched text wrapped and labelled, instructions inside it ignored, per-run domain allow
lists, and size caps. Prompt injection through fetched pages is the main risk, which is why this
waits until the rest is measured.

## M10 — hosted deployment

A Dockerfile, a Postgres option for the store (SQLite stays the default), TLS termination
guidance, and the M7 auth in front. Not before M6 and M7: a network-reachable server that runs
model-written code needs both.

## Deferred, and why

- **LangChain / LiteLLM / an SDK per vendor** — the OpenAI-compatible shape covers every free
  provider in ~250 lines, and owning the retry path is the point: free tiers fail by rate limit,
  and the router has to decide whether to wait or move (ADR-001, ADR-002).
- **A vector database** — not needed until there is memory worth retrieving (M8), and retrieved
  context costs the same per-minute tokens as everything else.
- **Fine-tuning models** — the request said "fine tune this", meaning *refine the idea*. Model
  fine-tuning is not needed for orchestration, and free API tiers do not offer it.
- **Rotating several keys of one provider** — pooling accounts to get around a free limit breaks
  most providers' terms. Cadre spreads work across *different* providers and respects each key's
  limits.
- **Local models as the default** — this laptop (i3-1215U, 7.7 GB single-channel RAM, no GPU)
  runs a 1.5B q4 at ~3.4 tok/s and a 3B at 0.96 tok/s while thrashing. Ollama, llama.cpp and
  LM Studio stay available as `fast`-tier fallbacks, not the engine.
- **A browser-free desktop window (Tessera's approach)** — an organisation-scale tool needs a
  shareable web UI, so the port is locked down instead (ADR-010).
