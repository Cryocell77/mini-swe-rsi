# mini-swe-rsi

English | [简体中文](README.zh-CN.md)

An online RSI framework with [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) as the initial Harness. After solving a task, the agent can modify its own Harness. Independent host checks decide whether to adopt the candidate. All roles use the same model.

Each round starts with a coding task, either from SWE-bench or a local project with a free-text request. The agent analyzes its execution trace, designs and implements an improvement when warranted, and submits a candidate. The host tests the candidate before using it on the next task. If there is no actionable improvement, the loop moves on.

## Installation

Requires Python 3.12, [uv](https://github.com/astral-sh/uv), and Docker. SWE-bench evaluation on ARM hosts also requires support for linux/amd64 containers.

Run from the repository root:

```bash
uv sync --locked
uv venv .eval-venv
uv pip install --python .eval-venv/bin/python -r evaluator.lock.txt
uv pip install --python .eval-venv/bin/python --no-deps .
docker build -t mini-swe-rsi-runtime:0.1 runtime
```

Upstream source is included in this repository; no source download is needed during installation. `.eval-venv` runs independent evaluation. Versions and dependencies are recorded in [upstream.lock.json](upstream.lock.json), [uv.lock](uv.lock), the [container requirements](runtime/requirements.lock), and the [evaluator requirements](evaluator.lock.txt).

## Quick start

Copy [.env.example](.env.example) to `.env` and fill in the service URL, API key, and model name.

Prepare a dataset, then run one task:

```bash
uv run mini-swe-rsi online --run runs/lite-smoke --data data/swe-bench-lite --split test --task-limit 1
```

See [Evaluation](#evaluation) for dataset preparation and [Data requirements](docs/architecture.md#data-requirements) for the directory format.

`online --data PATH` is required for both new and resumed runs; there is no default dataset directory.

`--task-limit N` selects the first N tasks in the split's original order. Omit it to run the entire split. If omitted, `--split` uses the manifest default. Use a separate `--run` directory for each experiment.

## Local single-task input

Specify a source directory and a verification command, then enter your request:

```bash
uv run mini-swe-rsi single \
  --workspace /path/to/project \
  --check 'python -m unittest discover -s tests -v'
```

The source is copied into `/testbed` in an isolated container. The original directory is unchanged. Keep the run directory and version store outside `--workspace`; using this repository itself as the workspace requires an external store and run path. The default `mini-swe-rsi-runtime:0.1` image supports the Python standard library and unittest, but does not include pytest. For other projects, use `--image IMAGE` to select a local Linux image containing bash, git, and the project dependencies. Containers cannot access the network to install dependencies.

When saved single-task promotions exist, the CLI asks you to **select the original or a promoted Harness before entering the task**. Promoted versions are listed newest first. Enter a full version number such as `V000003`, or press Enter to select the latest. The original version always comes from the integrated initial Harness.

![Single-task version selection demo](docs/images/single-input.gif)

*The demo uses example version data and does not call a model. Its prompts are in Simplified Chinese.*

For scripts, pass the version and request explicitly to skip the prompts:

```bash
uv run mini-swe-rsi single \
  --workspace /path/to/project \
  --check 'python -m unittest discover -s tests -v' \
  --version original \
  --prompt 'Fix parsing of empty input and add regression tests'
```

Use `--version V000003` to select a saved promotion. `--store PATH` sets the independent version store, which defaults to `runs/single-input/`. Promoted single-task Harnesses live in `versions/Vxxxxxx/` within that store; execution records live in `executions/`. They are separate from SWE-bench evaluation versions. `--run PATH` selects a run directory that must not already exist. Use a new directory for each request. Saved versions include their origin, parent, and source hash. Versions incompatible with changed framework code are rejected.

### Independent verification and results

Each verification starts a fresh container from the same input snapshot, applies the task patch, and runs `--check` in `/testbed`. Exit code 0 means success. The default timeout is 300 seconds; change it with `--check-timeout SECONDS`. Passing the task alone does not guarantee promotion: the existing RSI diagnosis, improvement, and candidate checks must also complete. No new version is saved when there is no actionable improvement. Single-task candidates must pass task verification to qualify for promotion.

Prefer an independent acceptance script over relying only on project tests the agent can edit:

```bash
uv run mini-swe-rsi single \
  --workspace /path/to/project \
  --verify-file /path/to/acceptance.py \
  --check 'python /verification/verify'
```

`--verify-file` is frozen at the start of the run and mounted read-only as `/verification/verify` only in the verification container. It is excluded from the solver's source copy even when stored inside the workspace. The reliability of an ordinary `--check` depends on what that command actually verifies.

Snapshots exclude `.git`, `.env*`, virtual environments, `node_modules`, and common Python caches. Symbolic links are unsupported. Install image dependencies outside `/testbed`. The input snapshot and verification command are saved in the run directory, and the run image remains available locally for inspection.

Each execution prints its run directory. Use `mini-swe-rsi report --run PATH` to inspect the report. The initial task patch is at `tasks/0001-single-task/patch.diff`. Candidate patches are under the corresponding `proposals/` entry at `check/attempt_*/patch.diff`. Verification logs are under `private/`. Review the report before applying a patch to your project.

## Evaluation

### Full SWE-bench Lite

The official [SWE-bench Lite](https://www.swebench.com/lite.html) test split contains 300 tasks. Prepare the task data from Hugging Face:

```bash
uv run mini-swe-rsi prepare \
  --dataset SWE-bench/SWE-bench_Lite \
  --split test \
  --data data/swe-bench-lite
```

`prepare` uses datasets from `.eval-venv` to save the original tasks, their order, and source hashes. It does not call a model or download task images. Add `--revision COMMIT` to pin the dataset revision. The output directory must not already exist.

Run the complete test split without `--task-limit`:

```bash
uv run mini-swe-rsi online \
  --run runs/lite-test \
  --data data/swe-bench-lite \
  --split test
uv run mini-swe-rsi report --run runs/lite-test
```

Start with the one-task check above. Full evaluation requires a new run directory; an existing frozen selection cannot be expanded. The first run prepares official linux/amd64 images for the selected tasks and records their identities. Allow sufficient download time and disk space for all 300 tasks.

### Other datasets

`--dataset` also accepts other Hugging Face datasets in SWE-bench format, or a local JSON/JSONL task file:

```bash
uv run mini-swe-rsi prepare \
  --dataset /path/to/tasks.jsonl \
  --split test \
  --data data/custom
uv run mini-swe-rsi online --run runs/custom --data data/custom --split test
```

Local JSON must contain a list of task objects; JSONL must contain one task per line. Each record needs `instance_id`, `repo`, `base_commit`, `version`, `problem_statement`, `test_patch`, `FAIL_TO_PASS`, and `PASS_TO_PASS`. The repository/version must be supported by the integrated SWE-bench evaluator and have a corresponding official task image. General question-answering or arbitrary coding data cannot use this evaluation entry point directly.

### Score accounting

The report's `evaluation` field counts each task's **initial attempt**. `selected_tasks` is the planned task count, `scored_tasks` is the scored count, and `resolved_tasks` is the number that passed. Once all selected tasks have been scored, `resolved_rate = resolved_tasks / selected_tasks`; until then, it is null. Candidate reruns and mechanism checks are stored separately and do not increase the resolved count.

This is task-by-task online RSI evaluation: the Harness can change between tasks. Running on a test split does not make it an independent evaluation of a fixed Harness. Report the online update procedure, dataset revision, task order, and model settings alongside any results.

SWE-bench scoring uses fresh containers from frozen `linux/amd64` task images, with 2 GiB memory, one CPU, 256 PIDs, and networking disabled. Test execution has a 300-second timeout; the host allows 360 seconds for the complete scoring subprocess. These limits can affect results and must be reported when comparing evaluations. Recipe downloads and image retrieval occur on the host. Empty patches, patch application failures, and test timeouts count as unresolved tasks; missing or invalid evaluation reports are infrastructure failures and trigger rescoring of the original patch, subject to the retry limit.

## Reports and resuming

Report generation does not require a model service:

```bash
uv run mini-swe-rsi report --run runs/lite-smoke
```

Reports are written to `online_report.md` and `online_report.json`. They include task scores, accepted/rejected/skipped candidates, model calls, and token usage. Source versions are in `versions/`; host decisions are in `decisions/`.

Resume an existing online run with the same arguments:

```bash
uv run mini-swe-rsi online --run runs/lite-smoke --data data/swe-bench-lite --split test --task-limit 1
```

Completed tasks are not rerun. Task selection, source code, model, and service configuration must match the original run. Interrupted model workflows require [explicit recovery authorization](docs/architecture.md#interruption-and-recovery).

## Tests

```bash
uv run pytest -q
RUN_DOCKER_TESTS=1 uv run pytest -q
```

The second command includes Docker integration tests with fixture model responses and grading. To check your own model service, run a one-task command above.

## License

This project is licensed under the [MIT License](LICENSE). Bundled upstream code retains its original copyright notices and licenses; see [upstream sources and adaptations](docs/upstream_adaptations.md).

## Documentation

- [RSI architecture](docs/architecture.md): the seven-step workflow, candidate checks, data requirements, resource limits, and recovery.
- [Upstream sources and adaptations](docs/upstream_adaptations.md): provenance, licenses, and changes from upstream.

Both the online and improvement protocols are currently v8. Older run directories require the corresponding code version. Acceptance means a candidate passed this round's criteria; benefits across tasks need separate evaluation.
