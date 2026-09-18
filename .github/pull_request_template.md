## What and why

<!-- One or two sentences. Link the issue if there is one. -->

## Checklist

- [ ] `uv run pytest -q` and `uv run ruff check src tests` pass
- [ ] A test covers the change (a bug fix includes the test that would have caught it)
- [ ] A behaviour, API or security decision has an ADR in `docs/ARCHITECTURE.md`
- [ ] `/api/v1` changed → `tests/snapshots/openapi-v1.json` regenerated on purpose and explained here
- [ ] `CHANGELOG.md` updated
- [ ] No keys, tokens or `.env` files in the diff
