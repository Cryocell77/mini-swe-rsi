# Step-5 interruption recovery, 2026-10-15

The online dev30 run stopped at R0010, step 5, when candidate P0009's first solver attempt returned HTTP 500. Tasks 1–10 and steps 1–4 already have durable evidence. The existing explicit restoration gate admitted only stage indices 0–3, so it could not continue this unfinished check.

The host and container now admit index 4 only with an explicit `resume_incomplete_check: true` authorization, an existing check plan, no completed candidate result, and unchanged candidate bytes. The host additionally checks the frozen `candidate_check_source` against the authorization. Stages after step 5 remain excluded. The normal phase write restrictions and original maximum of two infrastructure attempts remain in force. This change does not itself restore, repeat, approve or promote an experiment round.

A related evidence lookup now chooses the durable host result matching the record returned by `task_retry`, including attempt 1. Previously the candidate check inspected attempt 0 even after a permitted infrastructure retry completed in attempt 1, losing its execution status and metrics.

Regression tests cover explicit authorization, absent/changed frozen source, completed-result rejection and second-attempt evidence attribution. Upstream SICA sources and agent strategy are unchanged.

The requested real continuation was blocked during identity preflight: `.env` requests the frozen `deepseek-v4.1-flash`, but authenticated `/models` no longer advertises that ID. It advertises `deepseek-v4-flash`, whose equivalence is unverified. No inference request was issued, no pause marker was cleared, and no frozen control contract was migrated. Restore the original endpoint model identity before preparing an explicit, recorded control-contract migration and resuming. If selecting a different model, use a separate run rather than mix identities.

Validation: full suite with real Docker checks, 319 passed in 208.71 seconds; authorization suite rerun after adding the retry-evidence test, 7 passed in 5.92 seconds (real Docker enabled). The additional retry-evidence case was added after full-suite collection and is covered by that targeted run. `git diff --check` passed. Results are retained in `runs/step5-resume-engineering-20261015/`.
