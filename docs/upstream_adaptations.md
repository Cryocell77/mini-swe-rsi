# Upstream sources and adaptations

English | [简体中文](upstream_adaptations.zh-CN.md)

## Source provenance

| Component | Repository | Pinned commit | Purpose |
|---|---|---|---|
| SICA | `MaximeRobeyns/self_improving_coding_agent` | `ed8275dca4d3c5dbf77229964351fe9b424797dc` | Improvement task, seven-step definitions, roles, committee, and budget sources |
| mini-swe-agent | `SWE-agent/mini-swe-agent` | `04d809ceab9df28f9adaed044884180159172930` | Initial Harness agent, model, environment, config, and utils |
| SWE-bench | `SWE-bench/SWE-bench` | `726c5461e2ef52d83cf1ea2107870a8bb3328d57` | Official evaluation assets |

Source versions and original file hashes are recorded in [upstream.lock.json](../upstream.lock.json). Required source is included under `src/mini_swe_rsi/`; installation and tests do not require a download script.

- `harness/template/minisweagent/`: the pinned mini source template; its license is retained in `harness/LICENSE.mini`.
- `workflow/task/` and `workflow/sources/`: original SICA task, role, and seven-step definitions, with their MIT license. Budget reference sources are kept in `workflow/provenance/` for provenance checks only; their upstream agents and providers are not executed.
- `evaluation/swebench/`: the SWE-bench implementation. Internal imports use `mini_swe_rsi.evaluation.swebench`; dataset names, image names, and evaluation recipes remain unchanged. The license is in `evaluation/LICENSE.swebench`.

## Building H0000

[driver.py](../src/mini_swe_rsi/driver.py) creates H0000 from mini-swe-agent 2.4.6. [seed.py](../src/mini_swe_rsi/harness/seed.py) applies two changes to the copy:

1. [mini-no-billing.patch](../src/mini_swe_rsi/assets/mini-no-billing.patch) removes price calculations, monetary totals, and billing-related configuration, template variables, and trajectory fields. Before/after hashes are recorded in the [patch manifest](../src/mini_swe_rsi/assets/mini-no-billing.json).
2. [mini-online-slim.json](../src/mini_swe_rsi/assets/mini-online-slim.json) removes the standalone mini CLI, batch runners, and bundled benchmark configurations.

The archived root entry point runs tasks and improvements through the project bridge. Retained mini APIs are available for offline candidate tests; the standalone `python -m minisweagent` CLI is removed. These changes apply only when creating H0000 and do not rewrite existing candidates.

## Main differences from upstream

A host-owned online loop replaces SICA's batch runner. Each task is followed by diagnosis, improvement, and checking; a passing candidate becomes the next version. The seven-step and role definitions come from the retained SICA sources and run through a text operation protocol.

Every role calls the same model configured in `.env`. Candidates use existing dependency images and can edit source, configuration, and tests, but cannot install new dependencies. Data can come from Hugging Face or local SWE-bench records. The CLI follows the selected split's order, and task and image identities are frozen per run.

[grader.py](../src/mini_swe_rsi/evaluation/grader.py) reuses evaluation adapter code from the earlier local project, adding image identity checks, empty-patch handling, timeout classification, and container isolation. It calls official SWE-bench evaluation assets. Patches are extracted relative to the task image's initial HEAD to exclude changes from image preparation commits.

Initial attempts, candidate reruns, and mechanism comparisons are stored separately. See [RSI architecture](architecture.md) for execution rules and artifact layout.
