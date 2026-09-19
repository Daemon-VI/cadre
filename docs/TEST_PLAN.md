# Cadre — Test Plan

_2026-09-16 · v0.1 · SDLC phase 4 (verification). Requirements: `SRS.md`._

## Strategy

| Level | What | How | Network |
|---|---|---|---|
| Unit | parsers, limiter maths, tally, plan validation, path confinement | pure functions, fake clocks | none |
| Component | provider adapter, router, each workflow pattern | `httpx.MockTransport`; `ScriptedProvider` answering per agent | none |
| Integration | run lifecycle, resume, cancel across "processes", API + stream | `RunManager` on a temp `CADRE_HOME`; FastAPI `TestClient` | none |
| System (offline) | all four templates end to end | `--demo` provider; real `unittest`/`compileall` checks executed | none |
| System (live) | real free models | `cadre run …` on Groq + Google AI Studio keys | run 2026-09-17 (M5): all five templates; table in PROJECT_STATE |
| Manual | CLI and HTTP smoke | `cadre run --demo`, `cadre serve` + curl | none |

Every test runs with `CADRE_NO_KEYRING=1` and a temporary `CADRE_HOME`, so the suite never touches
the real credential store or `~/.cadre`.

```bash
uv run pytest -q          # 170 passed, 1 skipped, ~24 s on the i3-1215U (2026-09-17, after M5)
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

## v1.0 traceability (tests named before they exist, 2026-09-17)

| Requirement | Tests (file · test) | Milestone |
|---|---|---|
| FR-10 AC-10.1 per-model buckets, sourced | `test_catalogue::test_every_free_preset_model_has_a_source_and_date`, `…gemini_free_models_are_separate_buckets`, `…deepseek_is_paid_and_free_tiers_carry_their_policy`, `…chat_model_filter` | M6 ✅ |
| FR-10 AC-10.2 day clocks | `test_clocks::test_pacific_day_key_and_reset`, `…utc_is_unchanged_for_v01_rows`, `…rolling_window_frees_hour_by_hour`, `…utc_counts_carry_over_when_the_clock_changes`, `…daily_limit_on_a_pacific_clock_waits_for_pacific_midnight`, `…rolling_counters_reload_from_hourly_rows`, `test_catalogue::test_old_config_without_new_fields_uses_preset_values` | M6 ✅ |
| FR-10 AC-10.3 refresh | `test_catalogue::test_refresh_reports_added_and_removed_and_keeps_overrides`, `…refresh_discovers_openrouter_free_models_only`, `…a_provider_that_cannot_list_is_reported_not_fatal` | M6 ✅ (live: unverified) |
| FR-12 privacy | `test_privacy::test_private_run_never_calls_a_training_provider`, `…excluded_models_are_recorded`, `…private_run_with_nothing_left_fails_before_any_call`, `…standard_run_uses_the_preferred_model` | M6 ✅ |
| FR-11 AC-11.1 ledger | `test_forecast::test_usage_ledger_by_day_provider_model`, `test_api::test_usage_endpoint` | M7 ✅ |
| FR-11 AC-11.2 forecast | `test_forecast::test_no_history_is_labelled_estimated`, `…measured_history_uses_median_and_p90`, `…verdicts_fit_now_wait_days_cannot`, `…used_quota_counts_against_today`, `…percentile_is_nearest_rank`, `test_api::test_forecast_endpoint` | M7 ✅ (estimates unverified until M5) |
| FR-11 AC-11.3 reserve | `test_forecast::test_reserve_pct_shrinks_daily_caps` | M7 ✅ |
| FR-13 AC-13.1 edit | `test_edit_tools::test_edit_replaces_exactly_one_match`, `…zero_or_many_matches_report_the_count`, `…edits_are_versioned`, `…crlf_files_accept_plain_newline_edits` | M8 ✅ |
| FR-13 AC-13.2 ranges, search | `test_edit_tools::test_read_line_range`, `…search_is_capped_and_confined` | M8 ✅ |
| FR-13 AC-13.3 repo map | `test_edit_tools::test_repo_map_lists_defs_and_respects_the_cap`, `…repo_map_of_cadre_itself_stays_under_the_default_cap`, `…agents_with_file_tools_get_the_map_others_get_the_listing` | M8 ✅ |
| FR-13 AC-13.4 measured saving | `test_edit_tools::test_edit_saves_more_than_its_schema_costs` | M8 ✅ (estimator tokens; live pending M5) |
| FR-8 AC-8.1–8.2 refusals | `test_project::test_non_repo_is_refused`, `…dirty_tree_is_refused_unless_allowed` | M9 ✅ |
| FR-8 AC-8.3–8.4, 8.6 branch, commits, result | `test_project::test_run_commits_on_its_own_branch_and_leaves_the_owner_alone`, `…engine_records_stay_out_of_the_repo_and_cleanup_keeps_the_branch` | M9 ✅ |
| FR-8 AC-8.5 repo checks from base, `.cadre/` protected | `test_project::test_repo_checks_come_from_the_base_commit`, `…agents_cannot_write_cadre_dir` | M9 ✅ |
| FR-8 AC-8.7 resume on same branch | `test_project::test_resumed_project_run_keeps_its_branch` | M9 ✅ |
| NFR-10 owner's tree untouched | `test_project::test_run_commits_on_its_own_branch_and_leaves_the_owner_alone` (HEAD, branch, porcelain compared) | M9 ✅ |
| FR-9 AC-9.1–9.2 park and resume | `test_multiday::test_daily_limit_parks_then_resumes_without_rebilling`, `…minute_limits_still_wait`, `…serve_resumes_due_parked_runs`, `…scheduler_command_is_shown_not_run`, `test_router::test_exhausted_quota_parks_with_a_reason_that_names_the_model`, `…a_long_minute_block_still_fails_instead_of_parking` | M10 ✅ |
| FR-9 AC-9.3 cumulative budgets | `test_multiday::test_budgets_accumulate_across_resumes`, `…max_days_stops_the_run`, `…max_tokens_per_day_parks_until_tomorrow` | M10 ✅ |
| NFR-11 honest forecasts | `test_forecast::test_no_history_is_labelled_estimated`, `…measured_history_uses_median_and_p90` | M7 ✅ |
| Migration of a v0.1 database | `test_migration::test_v01_database_opens_and_keeps_rows` | M6 ✅ |

## Distribution traceability (D0, 2026-09-18)

| Requirement | Test or check |
|---|---|
| AC-14.1 licence, no copyleft | `tools/check_licences.py` (CI `hygiene`); `test_distribution::test_licence_policy_flags_copyleft` |
| AC-14.2 three OSs × two Pythons | CI `test` matrix (`.github/workflows/ci.yml`) |
| AC-14.3 gitleaks, private user name | CI `hygiene` (gitleaks-action, `tools/check_history.py`); `test_history_check_patterns_catch_what_they_guard` |
| AC-14.4 owner identity on every commit | `tools/check_history.py` (CI `hygiene`) |
| AC-14.5 scheduler on three OSs | `test_linux_plan_is_a_systemd_user_timer`, `test_macos_plan_is_a_launchd_agent`, `test_apply_writes_runs_and_removes`, `test_every_minutes_is_bounded`; CI runs `systemd-analyze verify` / `plutil -lint` on the generated files |
| AC-14.6 headless keys | `test_no_keyring_means_env_only` |
| AC-14.7 `--allowed-host` | `test_allowed_host_names_are_exact`, `test_an_allowed_host_is_accepted_and_others_still_refused`, `test_serve_passes_allowed_hosts_and_refuses_wildcards` |
| AC-15.1 `/api/v1` and the alias share one lock | `test_v1_and_the_alias_share_one_lock`; `test_api.py` now calls `/api/v1` |
| AC-15.2 OpenAPI snapshot | `test_openapi_snapshot_pins_the_v1_contract` (`tests/snapshots/openapi-v1.json`, 24 paths) |
| AC-16.1 wheel runs a demo | `tools/wheel_smoke.py` (CI `wheel`, Ubuntu and Windows) |
| AC-16.2…16.4 PyPI, standalone builds, image | `release.yml`: tag = version check, TestPyPI → PyPI, `packaging/build_binary.py` smoke (`--version` + demo) on three OSs, image smoke (uid 10001, demo run) |
| AC-16.3 frozen-build behaviour | `test_a_frozen_build_runs_checks_with_the_python_on_path`, `test_version_flag` |
| AC-17.1 five tools, none can approve | `test_mcp::test_exactly_five_tools_and_none_can_approve` |
| AC-17.2 client of `cadre serve`, autostart | `test_autostart_starts_serve_when_nothing_answers`; `tools/mcp_smoke.py` (real stdio) |
| AC-17.3 approvals stay with the human | `test_a_run_waits_for_a_human_and_mcp_cannot_open_the_gate` |
| AC-17.4 project default | `test_start_run_defaults_to_the_hosts_first_root` (roots round-trip, cwd repo, none, `""`) |
| AC-18.1…18.3 action: PR body, parked comment, trusted triggers, least permissions, goal only via env | `test_action_formats_outputs_body_and_parked_comment` |
| `cadre provider add-from-env` (free presets only, key stays in the environment) | `test_add_from_env_adds_free_providers_only` |
| `cadre run --result-json` | `test_result_json_for_scripts` |
| MCP project default order (`CADRE_PROJECT`, `CLAUDE_PROJECT_DIR`, roots, cwd repo) | `test_project_vars_come_before_roots_and_cwd` |
| AC-17.5 no token in results | `test_a_run_waits_for_a_human_and_mcp_cannot_open_the_gate`; `tools/mcp_smoke.py` |
| AC-17.3 MCP can never skip the exec approval (the 1.0.0/1.0.1 `allow_exec` bypass) | `test_mcp::test_start_run_can_never_skip_the_exec_approval`; `test_exactly_five_tools_and_none_can_approve` asserts no `allow_exec` in any schema |
| AC-22.1 runner, pinned image, `allow_unpinned` | `test_containers::test_a_container_check_needs_a_pinned_image`, `test_container_settings_on_a_subprocess_check_are_refused`, `test_org_files_carry_the_runner` |
| AC-22.2 every container flag | `test_every_required_flag_is_on_the_command_line`, `test_the_user_is_never_root`, `test_the_name_is_derived_from_the_run_id` |
| AC-22.3 one mount, environment allowlist | `test_the_workspace_is_the_only_mount`, `test_nothing_wider_than_a_workspace_is_mounted` (home, root, `CADRE_HOME`, `.git` directory, comma), `test_a_worktree_with_a_git_file_is_mountable`, `test_only_allowlisted_names_pass_and_keys_never_do`, `test_a_credential_name_cannot_be_allowlisted` |
| AC-22.4 timeout, cap, exit code, what was used | `test_exit_code_output_cap_and_what_was_used_are_recorded`, `test_a_timeout_kills_the_named_container`, `test_exit_137_is_explained` |
| AC-22.5 never pulls | `test_a_missing_image_fails_with_the_pull_command_and_nothing_runs`, `test_a_missing_runtime_is_named` |
| AC-22.6 Podman | `test_podman_takes_the_same_flags_and_keeps_the_owners_uid` |
| AC-22.7 approval policy | `test_container_only_runs_a_contained_check_without_asking`, `test_container_only_still_asks_before_a_check_that_runs_as_the_owner`, `test_by_default_a_contained_check_asks_and_the_prompt_says_where_it_runs`, `test_the_stored_option_survives_a_resume` |
| AC-22.7b `.cadre/checks.yaml` checks are validated too (credential-name env refused) | `test_containers::test_a_credential_name_cannot_be_allowlisted` covers the model; `.cadre/checks.yaml` goes through the same `CheckSpec.model_validate` (project.py load, runs.py rebuild) |
| ADR-031 a check cannot make Cadre's git run planted code | `test_project::test_cadres_commit_never_runs_what_a_check_planted_in_git` (git-directory and repointed-file, each with a control that plain git *does* run it), `test_a_check_that_changes_git_is_failed_and_the_pointer_restored` |
| AC-22.8 containment, real containers | `tests/test_containment.py` in CI job `containment` (Docker and Podman, Ubuntu): network, writes outside `/work`, planted key, fork bomb, memory cap, timeout kill, and the M11 unit converter's tests |
| AC-22.9 front ends show the runner | extension `format.test.ts` ("check, review and vote events"); dashboard `where()` and MCP `DATA_KEYS` by inspection |
| AC-23.1 roles and capability checks | `test_accounts::test_roles_are_nested_least_to_most`, `test_a_viewer_can_read_but_not_run_or_manage`, `test_a_member_may_run_and_approve_but_not_manage_users_or_providers`, `test_only_an_admin_sees_users_and_the_audit_log` |
| AC-23.2 tokens hashed, revoke/disable | `test_tokens_are_stored_hashed_never_in_the_clear`, `test_a_revoked_or_disabled_user_cannot_authenticate`; `test_cli::test_user_and_token_lifecycle_through_the_cli` |
| AC-23.3 bootstrap from the owner token | `test_bootstrap_turns_the_owner_token_into_the_admin`; the existing API tests authenticate as the bootstrapped admin |
| AC-23.4 `/me`, admin-only `/users` and `/audit`, no secret returned | `test_me_reports_the_caller`, `test_only_an_admin_sees_users_and_the_audit_log` |
| AC-23.5 audit log | `test_every_account_change_is_audited`; `test_cli::test_audit_command_shows_recent_actions` |
| AC-23.6 last admin protected | `test_the_last_enabled_admin_cannot_be_removed` |
| M13 migration v2 -> v4 keeps rows | `test_a_v2_database_migrates_and_keeps_its_rows` |
| AC-23.7 teams and membership | `test_teams_membership_and_budget_store`, `test_team_operations_validate`, `test_team_for_run_picks_the_right_team`; `test_cli::test_team_cli_lifecycle` |
| AC-23.8 per-team budget gate | `test_team_budget_gate_and_allowance_at_run_start` (runs/day refuses the 2nd run) |
| AC-23.9 model allowances | `test_model_allowance_matching`; `test_router::test_a_team_allowance_limits_which_models_a_call_may_use`; impossible allowance fails at start (`test_team_budget_gate_and_allowance_at_run_start`) |
| AC-23.10 action routing | `test_may_act_on_run_is_scoped_to_owner_team_or_admin`; `test_only_a_teammate_or_admin_can_cancel_a_team_run`, `test_starting_a_run_for_a_team_you_are_not_in_is_refused`, `test_admin_sees_teams_but_a_member_does_not`, `test_me_lists_the_callers_teams` |

## M14 traceability — memory across runs (2026-09-19, FR-24, ADR-036)

| Requirement | Test |
|---|---|
| AC-24.1/24.2 files, scopes, entry shape, bad entries skipped | `test_memory::test_parse_keeps_good_entries_and_names_the_bad_ones_without_crashing`, `test_a_bare_hand_written_line_is_an_approved_human_fact`, `test_an_entry_with_metadata_but_no_approver_is_pending`, `test_scopes_merge_and_an_unknown_scope_is_rejected`, `test_store_round_trips_and_reports_bad_entries` |
| AC-24.3/24.4 write paths (human + model proposal), approval flow, survives run finish | `test_finalize_approves_or_removes_a_proposal`, `test_memory_proposals_outlive_the_run_that_raised_them`; live: retrospective proposed 3 facts, one approved via `cadre approve` into project memory |
| AC-24.4 no memory approval over MCP | `test_mcp::test_exactly_the_read_and_run_tools_and_none_can_approve` (no approve tool; `cadre_memory_list` is read-only) |
| AC-24.5 key scan on both write paths, no echo | `test_the_key_scan_guards_both_write_paths`, `test_the_memory_key_scan_is_the_same_one_check_history_uses` |
| AC-24.6 deterministic selection under the cap, never split | `test_selection_is_pinned_first_then_by_overlap_then_recency`, `test_selection_respects_the_cap_and_never_splits_an_entry` |
| AC-24.7 builders/managers get memory, reviewers/voters do not | `test_memory_reaches_a_builder_but_not_a_reviewer_and_the_ledger_records_it` |
| AC-24.8 privacy filter | `test_pending_and_private_entries_are_filtered` |
| AC-24.9 ledger + forecast attribution | `test_memory_reaches_a_builder_…` (ledger + `memory.injected`), `test_the_forecast_includes_a_memory_line` |
| AC-24.10 scope permissions over the API | `test_a_member_writes_memory_a_viewer_only_reads_it` |
| AC-24.11 API/OpenAPI, MCP read-only tool | `test_distribution::test_openapi_snapshot_pins_the_v1_contract` (adds `/memory`), `test_mcp` tool set |
| AC-24.12 replay: a hostile entry cannot change policy | `test_a_hostile_memory_entry_is_carried_as_data_and_cannot_change_policy` |
| AC-24.13 measured, live | `PROJECT_STATE.md` "M14 measurement" (n = 1) |
| migration (usage.memory_tokens) | `test_a_pre_m14_database_gains_the_memory_tokens_column`, `test_migration::test_v01_database_opens_and_keeps_rows` |

## Live regressions (M5, 2026-09-17)

Each is a test built from what a real model or provider did:
`test_providers::test_gemini_thought_signatures_are_replayed_only_to_gemini`,
`…a_retired_model_is_classified_as_gone`, `…gemini_daily_and_minute_quota_errors_are_told_apart`,
`…provider_request_params_are_sent_with_every_call`;
`test_router::test_busy_model_rests_longer_each_time_and_recovers`,
`…a_model_that_answers_404_is_skipped_for_the_session`, `…a_tool_loop_stays_on_its_model`,
`…long_contention_waits_instead_of_failing` (fails on v0.1's 270 s cap),
`…a_daily_quota_429_blocks_the_model_until_its_reset`;
`test_engine::test_truncated_replies_are_recorded`, `…an_answer_that_should_have_been_a_file_is_nudged_then_saved`,
`…a_nudged_agent_that_writes_the_file_is_not_overridden`, `…repeated_identical_reads_are_not_executed_again`,
`…a_review_avoids_every_family_the_builder_used`, `…reviewers_see_the_written_files_inline`,
`…deliverables_are_read_from_the_real_template_tasks`;
`test_runs::test_forecast_is_recorded_once_per_run`; `test_forecast::test_project_size_sets_the_read_context`;
`test_cli::test_provider_add_without_a_key_fails_fast_instead_of_hanging`.

## Changed expectations

- 2026-09-17 (M10, ADR-017): `test_router::test_exhausted_quota_fails_with_a_reason_that_names_the_model`
  became `…parks_with_a_reason_that_names_the_model` — a daily block now parks the run. Its intent
  (the message names the model and the reason) is unchanged.

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

- Containment on Docker Desktop (Windows, macOS) and Podman outside Linux: the hostile probes run
  in Linux CI only. On this laptop one normal check ran in Docker Desktop (2026-09-19).
- A container check inside a real Cadre run with a live model (the policy is tested with a
  scripted model and a stubbed runner).

- The dashboard in a browser (the Chrome extension was not connected on 2026-09-16 or 2026-09-17).
- `cadre provider add` against a live endpoint (hidden prompt, keyring write on Windows).
- Concurrent writers: a CLI run and a server writing the same SQLite file at high rates.
- CI: `.github/workflows/ci.yml` is written (2026-09-18), but it has never run. It can't be pushed
  until Rithik grants the `gh` token the `workflow` scope, so Linux and macOS have never run the suite.
