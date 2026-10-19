# Upstream sources and adaptations

English | [简体中文](upstream_adaptations.zh-CN.md)

## Source provenance

| Component | Repository | Pinned commit | Purpose |
|---|---|---|---|
| SICA | `MaximeRobeyns/self_improving_coding_agent` | `ed8275dca4d3c5dbf77229964351fe9b424797dc` | Improvement task, seven-step definitions, roles, committee, and budget sources |
| mini-swe-agent | `SWE-agent/mini-swe-agent` | `04d809ceab9df28f9adaed044884180159172930` | Initial Harness agent, model, environment, config, and utils |
| SWE-bench | `SWE-bench/SWE-bench` | `726c5461e2ef52d83cf1ea2107870a8bb3328d57` | Official evaluation assets |

Upstream revisions and integrated source hashes are recorded in [upstream.lock.json](../upstream.lock.json). Required source is included under `src/mini_swe_rsi/`; installation and tests do not require a download script.

- `harness/template/minisweagent/`: the prepared mini runtime seed; its license is retained in `harness/LICENSE.mini`.
- `workflow/task/` and `workflow/sources/`: original SICA task, role, and seven-step definitions, with their MIT license. The runtime does not include unused upstream agent/provider implementations or budget reference copies.
- `evaluation/swebench/`: the SWE-bench implementation. Internal imports use `mini_swe_rsi.evaluation.swebench`; dataset names, image names, and evaluation recipes remain unchanged. The license is in `evaluation/LICENSE.swebench`.

## Building H0000

[driver.py](../src/mini_swe_rsi/driver.py) creates H0000 by copying the prepared mini-swe-agent 2.4.6 source from `harness/template/minisweagent/`. The seed already excludes monetary accounting, the standalone mini CLI, batch runners, and bundled benchmark configurations. Its file hashes are recorded under `mini.seed_files` in [upstream.lock.json](../upstream.lock.json).

Initialization verifies those hashes, copies the seed, and adds the archived root entry point and metadata. It does not apply patches or trim files at runtime, and it never rewrites an existing candidate. The root entry point runs tasks and improvements through the project bridge. Retained mini APIs remain available for offline candidate tests; the standalone `python -m minisweagent` CLI is absent.

## Main differences from upstream

A host-owned online loop replaces SICA's batch runner. Each task is followed by diagnosis, improvement, and checking; a passing candidate becomes the next version. The seven-step and role definitions come from the retained SICA sources and run through a text operation protocol.

Every role calls the same model configured in `.env`. Candidates use existing dependency images and can edit source, configuration, and tests, but cannot install new dependencies. Data can come from Hugging Face or local SWE-bench records. The CLI follows the selected split's order, and task and image identities are frozen per run.

[grader.py](../src/mini_swe_rsi/evaluation/grader.py) reuses evaluation adapter code from the earlier local project, adding image identity checks, empty-patch handling, timeout classification, and container isolation. It calls official SWE-bench evaluation assets. Patches are extracted relative to the task image's initial HEAD to exclude changes from image preparation commits.

Initial attempts, candidate reruns, and mechanism comparisons are stored separately. See [RSI architecture](architecture.md) for execution rules and artifact layout.
