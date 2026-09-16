# Cadre — Test Plan

_2026-09-16 · v0.1 · SDLC phase 4 (verification). Requirements: `SRS.md`._

## Strategy

| Level | What | How | Network |
|---|---|---|---|
| Unit | parsers, limiter maths, tally, plan validation, path confinement | pure functions, fake clocks | none |
| Component | provider adapter, router, each workflow pattern | `httpx.MockTransport`; `ScriptedProvider` answering per agent | none |
| Integration | run lifecycle, resume, cancel across "processes", API + stream | `RunManager` on a temp `CADRE_HOME`; FastAPI `TestClient` | none |
| System (offline) | all four templates end to end | `--demo` provider; real `unittest`/`compileall` checks executed | none |
| System (live) | real free models | `cadre run … ` against a Groq/Gemini key | **not yet run (M5)** |
| Manual | CLI and HTTP smoke | `cadre run --demo`, `cadre serve` + curl | none |

Every test runs with `CADRE_NO_KEYRING=1` and a temporary `CADRE_HOME`, so the suite never touches
the real credential store or `~/.cadre`.

```bash
uv run pytest -q          # 96 passed, 1 skipped, ~8 s on the i3-1215U (2026-09-16)
uv run ruff check src tests
```

The skipped test creates a directory symlink, which needs Developer Mode or admin rights on
Windows; the confinement check it covers (`resolve()` + `is_relative_to`) is the same code path
the `..` tests exercise.

## Traceability

| Requirement | Tests |
|---|---|
| FR-1.1 presets, any OpenAI-compatible URL | `test_api::test_keys_go_in_and_never_come_out`, `test_secrets::test_provider_without_a_key_is_skipped_with_a_warning` |
| FR-1.2 keys never stored or echoed | `test_secrets::test_a_leaked_key_never_reaches_the_database`, `test_providers::test_errors_never_contain_the_key`, `test_api::test_keys_go_in_and_never_come_out` |
| FR-1.3 test provider / list models | `test_providers::test_native_tool_call_usage_and_rate_are_parsed` (adapter); health/list: manual only |
| FR-1.4 tier/family/limits per model | `test_api::test_keys_go_in_and_never_come_out` (preset limits land in config) |
| FR-1.5 JSON tool protocol | `test_providers::test_json_protocol_for_models_without_function_calling`, `test_providers::test_to_json_protocol_rewrites_tool_turns`, `test_router::test_tools_unsupported_switches_to_json_protocol_and_retries` |
| FR-2.1 wait or move on | `test_quota::test_requests_per_minute_window_slides`, `test_quota::test_tokens_per_minute_waits_only_as_long_as_needed`, `test_router::test_waits_for_the_only_model_when_it_frees_soon` |
| FR-2.2 learn from headers | `test_providers::test_rate_headers_groq_and_openrouter_styles`, `test_quota::test_provider_headers_override_the_preset` |
| FR-2.3 429 / 401 / 5xx handling | `test_providers::test_classify_failures`, `test_router::test_rate_limit_falls_back_and_cools_the_model`, `test_router::test_rejected_key_disables_the_provider_for_the_session` |
| FR-2.4 oversize requests | `test_quota::test_request_larger_than_the_whole_minute_is_never_sent`, `test_router::test_too_large_request_excludes_that_model_only` |
| FR-2.5 daily counters persist | `test_quota::test_daily_counters_persist_through_the_book`, `test_quota::test_daily_limit_waits_until_utc_midnight_and_resets` |
| FR-2.6 explained exhaustion | `test_router::test_exhausted_quota_fails_with_a_reason_that_names_the_model`, `test_runs::test_no_configured_model_fails_with_directions` |
| FR-2.7 independence | `test_router::test_reviewer_is_routed_to_a_different_family`, `…prefers_another_family_over_tier`, `…single_family_proceeds_but_records_it_is_not_independent` |
| FR-3.1–3.2 org file + validation | `test_org::*` (7 tests) |
| FR-3.3 templates | `test_org::test_every_shipped_template_is_valid`, `test_runs::test_every_template_completes_in_demo_mode` (×4) |
| FR-4.1–4.3 agent / sequence / parallel | `test_engine::test_parallel_join_and_named_outputs`, `test_runs::test_resume_reuses_finished_steps_and_does_not_rebill` |
| FR-4.4 review loop, checks gate | `test_engine::test_failing_check_blocks_an_approving_reviewer_then_the_fix_passes`, `…gives_up_after_max_rounds`, `…unreadable_verdict_counts_as_not_approved` |
| FR-4.5 council | `test_engine::test_tally_rules` (×8), `…records_abstentions_and_leaders`, `…council_tie_goes_to_the_chair_and_bad_votes_abstain` |
| FR-4.6 manager | `test_engine::test_topo_waves_and_cycles`, `…validate_plan_reports_every_problem`, `…manager_repairs_its_plan_and_skips_dependents_of_a_failed_task`, `…manager_with_reviewer_reviews_each_task` |
| FR-4.7 approval gate | `test_engine::test_approval_gate_waits_for_a_decision_in_the_store`, `…rejected_gate_stops_the_run`, `test_api::test_approvals_can_be_decided_once` |
| FR-5.1–5.2 workspace | `test_workspace_tools::test_paths_that_leave_the_workspace_are_refused` (×12), `…symlink…` (skipped here), `…writes_are_versioned_and_reported`, `…binary_and_oversize_files` |
| FR-5.3 named checks, scrubbed env, exec approval | `test_workspace_tools::test_check_env_has_no_secrets`, `…checks_run_fail_and_time_out`, `test_runs::test_refused_execution_leaves_work_unapproved` |
| FR-5.4–5.5 notes, ask_human | demo runs exercise `post_note`; `ask_human` shares the approval path tested above — no dedicated test |
| FR-5.6 only listed tools | `test_engine::test_tool_outside_the_agents_list_is_refused` |
| FR-6.2 budgets | `test_engine::test_budget_stops_the_run_and_names_the_budget` |
| FR-6.3–6.4 events and usage | asserted inside the run and API tests (event kinds, totals, per-agent usage) |
| FR-6.5 resume | `test_runs::test_resume_reuses_finished_steps_and_does_not_rebill`, `test_runs::test_stale_active_runs_are_marked_interrupted` |
| FR-6.6 cancel | `test_runs::test_cancel_from_another_process_stops_a_waiting_run` |
| FR-7.2 API + stream | `test_api::test_demo_run_end_to_end_over_the_api` |
| FR-7.3 dashboard | `test_api::test_dashboard_is_served_with_a_strict_policy` (served, CSP, no `innerHTML`); `node --check app.js`; **rendering not verified in a browser** |
| FR-7.4 demo mode | `test_runs::test_every_template_completes_in_demo_mode` |
| NFR-3 API lock | `test_api::test_every_api_call_needs_the_token`, `…foreign_host_header_is_refused` |

## Defects found by the suite (2026-09-16)

1. `RunContext.emit(kind, …)` collided with approval events that carry a `kind` field → the
   research-desk template crashed at its approval gate. Fixed by making the event name
   positional-only.
2. **A key typed into a goal was stored unredacted**: a fresh process had not loaded any key into
   the redactor before `create_run` wrote the goal. Found by the planted-key test; fixed by
   `RunManager.load_keys()` before anything is stored.
3. `approval:` shorthand was not inferred as a step type; pydantic's error for it was unreadable.
   Fixed, with a friendlier message and a test.

## Not covered yet

- Real-model behaviour of any kind (M5): whether free models follow the JSON verdict/vote/plan
  shapes, how many repair turns they need, real token cost per template, real 429 behaviour.
- The dashboard in a browser (the Chrome extension was not connected on 2026-09-16).
- `cadre provider add` against a live endpoint (hidden prompt, keyring write on Windows).
- Concurrent writers: a CLI run and a server writing the same SQLite file at high rates.
- CI: `.github/workflows` not added — the repo has no remote.
