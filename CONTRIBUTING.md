# Contributing to Cadre

Thanks for helping. Cadre is small on purpose, so a few rules keep it that way.

## Set up

```bash
git clone https://github.com/Daemon-VI/cadre && cd cadre
uv sync
uv run pytest -q                 # no network, no key needed
uv run ruff check src tests
```

The tests use scripted models and mocked HTTP, so they need no key. Set `CADRE_HOME` to a
scratch directory if you try things by hand, so your real `~/.cadre` stays untouched.

## Rules

- **A decision gets an ADR.** If a change alters behaviour, the API, security or a trade-off,
  add or amend an entry in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the decision, why,
  what was rejected, and, for anything reachable from outside, a short threat model.
- **Tests first.** Every requirement in [`docs/SRS.md`](docs/SRS.md) has an acceptance
  criterion and a test ([`docs/TEST_PLAN.md`](docs/TEST_PLAN.md)). A bug fix comes with the test
  that would have caught it.
- **Claims are measured.** Numbers in the README and docs come from a run, a test or a dated
  source. If you haven't observed something, write "unverified".
- **The API contract is pinned.** A change under `/api/v1` changes
  `tests/snapshots/openapi-v1.json`. Regenerate it on purpose with
  `CADRE_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_distribution.py`, and explain the change in
  the pull request.
- **Front ends stay thin.** Routing, quota, runs and approvals live in the Python engine only
  (ADR-024). If a front end needs more, add it to the API with tests first.
- **No keys anywhere.** Test fixtures use obviously fake keys. Never commit `.env` files.
- **A new agent tool must earn its tokens.** Tool schemas are re-sent on every call and free tiers
  cap tokens per minute (ADR-021).
- **Free-tier presets are dated.** Change a limit only with a source, and update `CHECKED` and
  the preset's `source` field in `src/cadre/presets.py`.

## Pull requests

Keep them focused. Before opening one:

- `uv run pytest -q` and `uv run ruff check src tests` pass.
- `CHANGELOG.md` has a line under the unreleased version.
- Docs are updated if behaviour changed.

By contributing you agree that your contribution is licensed under Apache-2.0.
