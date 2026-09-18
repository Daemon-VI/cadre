# Cadre — objectives and features

_2026-09-18 · v1.0 programme (M0–M11), live on Groq and Google AI Studio. Requirements: `SRS.md`. Evidence: `PROJECT_STATE.md`._

## The problem

Multi-agent frameworks assume a paid API: they fan work out to several agents, retry freely, and
treat the bill as the only limit. On free tiers the limit arrives first, and it is a different
one: each key allows a few thousand tokens a minute and a few hundred or thousand requests a day,
and every agent in a team draws from the same small pools. Those frameworks also tend to let the
same model build and review its own work, and let a model summarise a vote it took part in, so
"review" and "decision" mean less than they say. And the people who would benefit most — a
student with free keys and an 8 GB laptop, or a small team without an AI budget — cannot run
local models big enough to plan.

Cadre is an organisation of AI agents, declared in one file, that runs on whatever free model
keys its owner has, stays inside every one of their limits, and makes review independent,
verification programmatic and decisions countable.

---

## Objectives

| # | Objective | How it is met | Evidence |
|---|---|---|---|
| **O1** | **Bring your own key, for any free model** — adding a model means adding its key | One OpenAI-compatible adapter covers every free provider; 15 dated presets (Groq, Gemini, OpenRouter, Mistral, Cohere, NVIDIA, Cloudflare, Z.ai, Hugging Face, Ollama, llama.cpp, LM Studio, OpenAI, Anthropic, DeepSeek) plus any custom URL; model discovery from `/models`; JSON tool protocol for models without function calling | `tests/test_providers.py` (mocked HTTP: tool calls, usage, errors, JSON protocol); live: Groq and Google AI Studio keys added by preset, `provider test`/`refresh` against both, five templates run (M5, 2026-09-17) |
| **O2** | **Stay inside every free tier** without the owner doing arithmetic | Per-model limiter for RPM, TPM, RPD, TPD with sliding minute windows and day counters on each provider's own clock, persisted in SQLite; rate-limit headers override preset numbers; 429 cools a model for `retry-after` (a daily-quota 429 until the model's reset) and the call falls back; 401/403 disables a provider, 404 a model; oversized requests never sent; a model more than 90 s away is skipped, one call waits up to 15 min in total, and daily limits park the run | `tests/test_quota.py`, `tests/test_router.py`; live: Groq's `x-ratelimit-*` headers seen in `GET /api/quota` (4,689 tokens left, reset 9.9 s); bursts produced `route.wait` events (M5) |
| **O3** | **An organisation is code** | One YAML file: agents (role, instructions, tier, tools, `diverse_from`), named checks, budget, workflow tree; type inference for short forms; every reference validated before the first model call, errors carry their path | `tests/test_org.py`; all five shipped templates validate |
| **O4** | **Independent review, programmatic verification** | Reviewers are routed away from the builder's model family, and a run records `independent: false` when no other family exists; in a review loop every check must pass and the reviewer rule must hold — a model can reject working code, never approve failing code | `tests/test_engine.py`, `tests/test_router.py`; live: reviewers routed to Qwen while builders used gpt-oss/Gemini; `independent: false` recorded whenever no unused family was left (every capstone review). Review *quality* is not assessed |
| **O5** | **Auditable decisions** | Council: independent proposals, critique rounds with anonymised peers, options consolidated by the chair, strict-JSON votes, tally in Python (`majority`, `supermajority`, `unanimous`, `plurality`), chair breaks ties and is marked as having done so, unparseable votes recorded as abstentions, dissent listed; `DECISION.md` carries the code-written tally table | `tests/test_engine.py` (tally rules, ties, abstentions) |
| **O6** | **Company mode** — a manager that delegates | Manager emits a JSON task graph; engine checks assignees, dependencies, cycles and size, asks for one repair, runs ready tasks in parallel waves under `max_parallel`, reviews each deliverable if a reviewer or checks are set, skips dependents of failed tasks, and has the manager integrate `REPORT.md` with a code-written task ledger | `tests/test_engine.py` (plan validation, waves, skip on failure) |
| **O7** | **Safe by construction** | Models name checks, never commands; checks run with a scrubbed environment, timeout, capped output and an `exec` approval unless `--allow-exec`; file tools confined to the run workspace; approval gates and `ask_human`; API on loopback with a bearer token, Host check, no CORS, strict CSP; keys only in the OS credential store or environment, redacted from everything stored | `tests/test_workspace_tools.py`, `tests/test_secrets.py`, `tests/test_api.py` |
| **O8** | **Bounded and resumable** | Per-run budgets for model calls, tokens, minutes (approval waits excluded) and parallelism, each stopping the run by name; per-agent turn caps and 4 KB observations; every finished step stored by path so a resumed run reuses it without re-billing; cross-process cancel | `tests/test_engine.py`, `tests/test_runs.py` (resume counts provider calls) |
| **O9** | **Observable** | Every model call (model, tokens, independence, waits, fallbacks), tool call, check, verdict, vote, plan and approval is an event; usage per agent/provider/model; live timeline, files, quota bars and approvals in the dashboard; `cadre show --events` in the terminal | `tests/test_api.py` (event feed), `tests/test_runs.py` |
| **O10** | **Runs on this laptop** — i3-1215U, 7.7 GB, no GPU | No local model required; all compute-heavy work is on the providers' side; SQLite, no services; offline demo mode for trying every pattern with no key | Demo runs of the four original templates succeed offline (project-finisher's demo ends `unapproved` by design: its checks fail); server 64.5 MB idle, 72.9 MB peak while a live run streamed (M5) |
| **O11** | **Finish an existing project, not just start new ones** | Project mode: a git worktree on `cadre/<run-id>`, commits per step, checks from the repo's own `.cadre/checks.yaml` at the base commit, a `project-finisher` template; the owner reviews a branch | NFR-10 test; live: capstone 2 finished a half-built package on `cadre/20260918-152845-13a59c` (8 tests pass), owner's HEAD/branch/porcelain unchanged (M11) |
| **O12** | **Outlast a day's quota** | Runs park on daily limits and resume after the provider's own reset, without re-billing finished steps | Fake-clock park/resume test (M10); a live run was interrupted and resumed next day without re-billing (M11); **no run parked live** — park is still fake-clock only |
| **O13** | **Use every free model a key reaches** | One quota bucket per free model with sourced limits and the provider's own day clock; `provider refresh` | Catalogue tests (M6); Google AI Studio free chat models listed from ai.google.dev pricing page, checked 2026-09-17 |
| **O14** | **Know before you run** | Usage ledger per day × provider × model; forecast from measured history (median, p90, n) or from template size, clearly labelled | Forecast tests (M7); live runs record `run.forecast`; the template estimate was recalibrated on six live runs (M5) and later runs used `measured, n = 1` |
| **O15** | **Keep private work away from training** | `privacy: private` excludes providers whose free tier trains on prompts (or might) | Routing test (M6); per-provider policy sourced 2026-09-17 |
| **O16** | **Small edits cost small tokens** | `edit_file`, line-range reads, `search`, a capped repo map; each kept only if measured cheaper | Token measurement in ADR-021/022 (M8) |

---

## Cost-driven design

Free tiers are priced in **tokens per minute**, not money, and that ceiling is the binding
constraint. Groq's free `openai/gpt-oss-120b` allows 8,000 tokens a minute. A team of five agents
whose prompts each run to ~1,500 tokens (system block, roster, rules, task, workspace listing,
team notes, tool schemas) spends ~7,500 tokens in one pass — nearly the whole minute before a
single reply is counted. Measured live (M5, 2026-09-17): the median first prompt of a step was
509–1,039 tokens depending on the template, and agents that read other agents' files sent 3.8–4.8k
per call.

That arithmetic produced the rules the code follows:

1. **Few tools, offered per agent.** There are eight tools (six in v0.1; `edit_file` and `search` were added in M8 on measured savings) and an agent sees only those its spec
   lists, because every schema is re-sent on every call. A new tool has to be used often enough
   to pay for its schema; anything rarer belongs inside an existing tool.
2. **Context that nearly everyone needs is injected, not fetched.** The workspace listing (≤ 40
   entries) and the last 12 team notes go into the prompt, which is cheaper than a tool round
   trip for each agent.
3. **Bounded turns.** Default 8 model turns per agent step, at most 4 tool calls per turn, then
   one closing call without tools. Review loops have `max_rounds`, managers `max_tasks`.
4. **Truncated observations.** A tool result re-enters the prompt at no more than 4 KB; check
   output is capped at 16 KB and quoted to reviewers as a short tail.
5. **Reserved output is counted.** A call's estimate is prompt characters ÷ 4 plus its
   `max_tokens`, so the limiter never admits a call that the provider would reject for size; the
   estimate is settled against the real usage when the reply arrives.
6. **Waiting beats failing.** A minute window drains in 60 s, so a model that frees within 90 s is
   waited for, one call may wait up to 15 min in total, and a run blocked only by daily limits
   parks until the reset — always saying which model it is waiting for and why.
7. **Spread across providers, not accounts.** Parallel capacity comes from adding different
   providers; Cadre never rotates several keys of one provider.
8. **Nothing runs unbounded.** Budgets on calls, tokens and minutes stop a looping team and name
   the budget that stopped it.

---

## Features

- `cadre provider add <preset>` with a hidden key prompt; keys in the OS credential store
- 15 provider presets with dated free-tier limits and their source; custom endpoints
- Model discovery, tiers (`strong`/`fast`), families, priorities, per-model limit overrides
- Quota-aware routing with header learning, cooldowns, fallback and downgrade rules
- Independence routing for reviewers and council members, recorded per call
- Org-as-YAML with validation and five templates: software team, decision board, startup
  company, research desk, project finisher
- Seven step types: agent, sequence, parallel (with join), review loop, council, manager, approval
- Named checks (`{python}` resolves to Cadre's interpreter, so stdlib checks need no setup)
- Shared, versioned, confined workspace per run; team notes board; `ask_human`
- Budgets, cancellation across processes, resume without re-billing
- Event log, per-agent usage, `DECISION.md` / `REPORT.md` / `plan.json` artefacts
- CLI, REST API with streamed events, local dashboard (no build step, no third-party code)
- Offline demo mode covering every pattern
