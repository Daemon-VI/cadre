# Cadre — a three-minute demo

Every command here was run, in this order, from a **clean `CADRE_HOME`** with **no API key**, on
Windows, against the published **1.3.0**. The output below is the real output, trimmed only where
marked `…`. Local paths are written as `<CADRE_HOME>`.

**The commands themselves take about 10 seconds in total** — the three minutes is the talking. The
whole demo is offline: it needs no key and spends no quota.

```bash
# a scratch home, so the demo never touches your real ~/.cadre
export CADRE_HOME=/tmp/cadre-demo        # Windows: $env:CADRE_HOME="$env:TEMP\cadre-demo"
```

---

## 1. Install — one command, no clone (5s cold)

```console
$ uvx --from cadre-ai cadre --version
cadre 1.3.0
```

*Say:* it ships as a package, a container image and standalone builds for three OSs. Nothing to
clone, no virtualenv to manage.

---

## 2. Ask the cost **before** spending anything (1s)

```console
$ uvx --from cadre-ai cadre forecast decision-board "Should we adopt a four-day week?" --demo
Forecast: fits now
  basis: no history, estimated from template size
  calls: median 18.2, p90 18.5 · tokens: median 17,746, p90 17,990
  left today across eligible models: unlimited requests, unlimited tokens
  council of 4: 1 critique round(s)
```

*Say:* the whole product exists because **free tiers limit requests and tokens, not money**. So the
first question is always "does this fit in what's left today?". `basis:` tells you whether the
number is measured from your own earlier runs or estimated from the org file — it never pretends to
know more than it does. (Here `unlimited` is just the offline demo provider; with a real key it
shows each model's remaining daily cap.)

---

## 3. Run an organisation of agents (1–2s in demo mode)

```console
$ uvx --from cadre-ai cadre run decision-board "Should we adopt a four-day week?" --demo
Run 20260920-150535-27a5a3 · decision-board · demo mode
Forecast: fits now
  …
15:05:35            run started · decision-board · models: demo-a/alpha-large, demo-b/beta-large, demo-a/alpha-small
15:05:35            ▶ council  w
15:05:35        cfo demo-a/alpha-large 388→69 tok
15:05:35        cfo   {"position": "…As Chief financial officer, I lean towards: Run a small pilot first.", …}
15:05:35        cto demo-b/beta-large 374→63 tok
15:05:35        cto   {"position": "…As Chief technology officer, I lean towards: Proceed now.", …}
15:05:35        cmo demo-a/alpha-large 375→63 tok (not independent — only one model family available)
15:05:35       risk demo-a/alpha-small 376→70 tok (not independent — only one model family available)
        …
15:05:35            vote (majority): A:2 B:2 → A (decided by chair)
15:05:35        ceo   Chief executive and chair (ceo) finished the task.
```

Three things to point at in that timeline:

- **Every call is priced as it happens** — `388→69 tok` is in, then out. Cost is never a surprise.
- **`not independent — only one model family available`.** Cadre routes a second opinion to a
  *different model family* on purpose, and when it can't, **it says so** instead of quietly passing
  one model's answer off as a review.
- **`vote (majority): A:2 B:2 → A (decided by chair)`** — the tally is computed in code, not by a
  model. A model can't miscount its own vote.

And the result it wrote:

```markdown
# Decision: Run a small pilot first

## Vote (counted by Cadre, not by a model)

Rule: **majority** · votes cast: 4 · decided by: **chair**

| Option | Votes |
|---|---|
| A. Run a small pilot first (chosen) | 2 |
| B. Proceed now | 2 |

| Member | Choice | Confidence | Reason |
|---|---|---|---|
| cfo | B | 0.60 | … cfo prefers B. |
| cto | A | 0.60 | … cto prefers A. |
| cmo | A | 0.60 | … cmo prefers A. |
| risk | B | 0.60 | … risk prefers B. |

Dissent: cfo, risk
```

*Say:* it records **who dissented**. A board paper that hides the split is worth less than one that
shows it.

---

## 4. What did that cost? (1s)

```console
$ uvx --from cadre-ai cadre runs
┌───────────────┬────────────────┬───────────┬───────┬────────┬───────────────┐
│ id            │ org            │ status    │ calls │ tokens │ goal          │
├───────────────┼────────────────┼───────────┼───────┼────────┼───────────────┤
│ 20260920-150… │ decision-board │ succeeded │ 15    │ 7925   │ Should we     │
│               │                │           │       │        │ adopt a       │
│               │                │           │       │        │ four-day      │
└───────────────┴────────────────┴───────────┴───────┴────────┴───────────────┘
```

15 calls, 7,925 tokens — against a forecast of ~18 calls / ~17,700 tokens. Over-estimating is the
safe direction, and the forecast says it is only an estimate.

**`cadre usage` is the cross-run ledger, and in demo mode it is empty:**

```console
$ uvx --from cadre-ai cadre usage
No usage recorded in the last 7 day(s).
```

That is honest, not broken: the demo models are not a configured provider, so they have no daily
cap to report against. With a real free key added (`cadre provider add groq`), the same command
shows each model's spend and its share of the cap — this is real output from a machine that had
been running Cadre on Groq:

```console
$ cadre usage --days 2
                         Usage, last 2 day(s) — share is of the full daily cap
┌────────────┬──────────┬─────────────────────┬──────────┬──────┬────────┬────────┬───────┬────────────┐
│ day        │ provider │ model               │ requests │ rpd  │ tokens │ tpd    │ share │ next reset │
├────────────┼──────────┼─────────────────────┼──────────┼──────┼────────┼────────┼───────┼────────────┤
│ 2026-09-19 │ groq     │ qwen/qwen3.8-27b    │ 24       │ 1000 │ 46,225 │ 200000 │ 23%   │            │
│ 2026-09-19 │ groq     │ openai/gpt-oss-20b  │ 9        │ 1000 │ 11,192 │ 200000 │ 6%    │            │
│ 2026-09-19 │ groq     │ openai/gpt-oss-120b │ 35       │ 1000 │ 47,006 │ 200000 │ 24%   │            │
└────────────┴──────────┴─────────────────────┴──────────┴──────┴────────┴────────┴───────┴────────────┘
```

(`next reset` is blank because Groq's free limits roll continuously rather than resetting at a
fixed hour; a provider like Google AI Studio shows a time there.)

*Say:* that is the product in one screen — **what each free model spent, and how much of today's
cap is gone.**

---

## 5. The three things you can't show offline

- **Checks in a container.** A check can declare `runner: docker` with a digest-pinned image, and
  then runs with no network, a read-only root, every capability dropped and the workspace as its
  only mount — proven against real Docker *and* Podman in Linux CI, where a check could not reach
  the network, write outside `/work`, read a planted key, fork past its limit or outlive its
  timeout. (Needs a container runtime; it is **not** proven on Windows or macOS.)
- **Teams.** Several people can share one server, each with a role and a hashed token, and a team
  can cap runs and tokens per day and be limited to certain models — checked when a run starts.
- **The GitHub Action.** Label an issue `cadre` and it works on a branch in a throwaway runner and
  opens a pull request. Only `OWNER`/`MEMBER`/`COLLABORATOR` can trigger it, and it never pushes to
  the default branch — a human merges.

## If someone asks "what doesn't it do?"

Say it plainly: containment is proven on Linux only; the Action's "left today" figure is wrong
because a fresh runner's ledger starts empty; run *reads* are not scoped by team; and memory across
runs, measured on one task, changed no outcome and cost about 94 tokens per model call. Those are
all written down in the README and `PROJECT_STATE.md`.
