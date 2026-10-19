# RSI architecture

English | [简体中文](architecture.zh-CN.md)

## Running a round

The active version `H_v` solves the current task. The host scores it independently, then gives its execution trace and public feedback to the same version for analysis. The improver copies `H_v` into a candidate and follows SICA's seven steps:

| Step | Action |
|---|---|
| 1–2: Diagnose | Archive analysis roles submit separate reports citing public evidence from the current task |
| 3: Design | Review the improvement proposal and choose acceptance criteria and a test plan |
| 4: Implement | Edit candidate source, configuration, and tests; run local checks |
| 5: Check | The host snapshots the candidate, reruns the task, scores it independently, and executes the declared mechanism test |
| 6–7: Record | Update `description.txt` and `agent_change_log.md` |

After steps 1–2, the orchestrator chooses `skip` or `improve`. `skip` ends the round. `improve` must identify improvement opportunities and continue through steps 3–7. Only step 4 permits changes to executable code.

After all seven steps, the host verifies the check results and source hashes before publishing `H_(v+1)`. If accepted, the next task and subsequent improvement use that version. If rejected, `H_v` remains active.

## Candidate checks

Step 3 selects one criterion:

- `current_task_failure_fixed`: fix the failure on the current task.
- `mechanism_parent_failed_candidate_passed`: avoid a task score regression and pass the same mechanism test that fails on the parent.

The mechanism criterion also requires a test plan:

```json
{
  "criterion": "mechanism_parent_failed_candidate_passed",
  "mechanism_plan": {
    "test_path": "tests/mechanism_check.py",
    "args": [],
    "timeout_seconds": 60
  }
}
```

The test path is fixed in step 3; the test code is written in step 4. The host overlays the same test onto both parent and candidate, then runs each under a read-only `/source`. Tests can use `RSI_SOURCE_ROOT` to locate the source root and write temporary files to `/tmp`.

Executable source cannot change after checking. The host compares execution hashes of the checked and final candidates to prevent adoption of untested code.

Mechanism tests should exercise an entry point shared by parent and candidate. If the parent fails to import a newly added function, the existing exit-code criterion may still pass, but that alone does not demonstrate improved behavior. Tests should assert expected results rather than only the presence of a new field or symbol.

## Data requirements

Use `prepare` to create a dataset directory from Hugging Face or local JSON/JSONL. `manifest.json` records provenance, the raw file hash, task order for each split, and the default split. `raw.json` contains SWE-bench task records. Task counts and split names are not fixed.

```text
data/
├── manifest.json
└── raw.json
```

`online --split NAME` selects a split; `--task-limit N` selects its first N tasks. Task order and selection are frozen in `online_task_selection.json` in the run directory. A run cannot switch splits, reorder tasks, or expand its selection. Recovery also rejects changed raw data or manifests.

Only official linux/amd64 images for selected tasks are prepared. Local images are preferred; otherwise they are pulled. Resolved image IDs and registry digests are saved to `image_inventory.json`. Recovery uses that record rather than resolving the latest tag again.

Existing `images/` metadata and `preflight/` records can be retained. When the manifest declares preflight evidence, the loader checks its hashes. These files are not required for all new datasets, and no fixed 30/30/100 split is required.

`online --data PATH` is required for both new and resumed runs. There is no default dataset directory. Newly prepared datasets use the specified split as their default.

## Isolation and permissions

Roles can read public feedback from completed tasks, diagnoses, check summaries, and accepted source versions. At launch, the public archive is copied into a read-only view. The live candidate is at `/home/agent/workdir`.

Source editing is allowed only in step 4. Steps 6–7 can write only descriptions and change logs. Candidates cannot access model keys, future tasks, reference patches, or private evaluation assets.

The host Broker forwards model calls. Containers use restricted session tokens, and candidate checks are bound to task, round, parent version, and session. Task tools, mechanism tests, and official evaluation run in isolated containers. Model containers reach the host through a relay and do not mount the Docker socket.

## Resource limits

| Item | Limit |
|---|---|
| Orchestrator, archive, solver, and reviewer roles | 500 iterations per role invocation |
| Coding role | 1,000 iterations per role invocation |
| One completion | Up to 5 provider requests |
| Task solving | 600 seconds |
| SWE-bench scoring container | 2 GiB memory, one CPU, 256 PIDs, no network; frozen `linux/amd64` image |
| SWE-bench scoring timeout | 300 seconds for tests; 360 seconds for the host scoring subprocess |
| Improvement workflow | No additional global or stage wall-clock deadline |
| Tool command | Declared range 5–1,800 seconds; actual maximum 600 seconds |
| Model context and output | Capacity read from authenticated service metadata |

Improvement shell output above 300,000 bytes is saved to a file, with an index and preview returned to the caller. Above 8 MiB, the sandbox is terminated and only a prefix marked incomplete and its hash are retained. Tool timeouts also clean up the sandbox.

Offline agent loop tests need an explicit exit condition and a finite step limit. `LimitsExceeded` means the allowance was exhausted; it is not a successful submission.

All models use service-declared context and output capacities, followed by a check of actual `prompt_tokens + completion_tokens`. The host does not load a local tokenizer or estimate tokens from character counts. Qwen-specific pre-request length and gross input-truncation checks are removed. Provider context errors still fail the request, and responses whose reported usage exceeds the frozen capacity are withheld from the agent. LiteLLM may still install `tokenizers` as a transitive dependency.

## Role operations

Structured operations use `sica-workflow` followed by raw JSON:

```text
sica-workflow {"op":"read","path":"/home/agent/workdir/README.md","start_line":1,"end_line":40}
sica-workflow {"op":"submit","report":"Full report body"}
```

Do not wrap the JSON object in shell quotes or include the completion sentinel in the report body. The current role's tool instructions define other operations and shell permissions.

## Artifact layout

| Path | Contents |
|---|---|
| `configuration.json`, `online_task_selection.json` | Configuration identity and CLI-selected task sequence |
| `online_state.json` | Active version, cursor, and run status |
| `versions/Hxxxx/` | Published source and version identity |
| `tasks/` | Initial-attempt `attempt_N` records and public feedback |
| `rounds/Rxxxx/` | Diagnoses, role reports, workflow state, and check plans |
| `proposals/Pxxxx/` | Candidate source, rerun results, and mechanism comparisons |
| `candidate_checks/`, `decisions/` | Check state and host decisions |
| `events.jsonl` | Model calls, tool calls, and state events |
| `private/` | Host-private records, including evaluation assets and recovery authorization |
| `online_report.json`, `online_report.md` | Summary reports |

Initial attempts stay under `tasks/`; candidate reruns stay under `proposals/` and are counted separately. Text traces use `trace.txt`; complete `trajectory.json` files remain private. Do not commit `.env`, private trajectories, or session tokens to Git.

## Interruption and recovery

Rerunning the same directory checks that tasks, source, model, service, and control code match. Committed decisions are not repeated. Evaluation infrastructure failures rescore the original patch without generating another answer.

Unfinished model workflows require `--resume-checkpoint PATH`. PATH must be a host authorization file under this run's `private/`, binding the original workflow state, candidate source hash, run identity, control manifest, and cumulative usage. Recovery creates a new model context and carries forward call and role-iteration counts. See [recovery.py](../src/mini_swe_rsi/recovery.py) for supported stages and fields.

The current protocols are `mini-swe-rsi-online-v8` and `sica-mini-improvement-v8`, with check policy revision 2. The protocol field is `protocol`; mechanism plan fields are `test_path`, `args`, and `timeout_seconds`. Host task records use the full-source `code_hash`; execution hashes separately identify the code actually tested.

## Code structure

`src/` exposes only the `mini_swe_rsi` package:

```text
src/mini_swe_rsi/
├── harness/       # Prepared initial Harness source and license
├── workflow/      # Seven-step state machine, bridge, roles, and provenance
├── evaluation/    # Isolated evaluator and integrated SWE-bench implementation
└── ...            # Online scheduling, model service, recovery, and reports
```

`harness/template/minisweagent/` contains the prepared initial Harness source, not a host top-level package. Each candidate generation retains its own `minisweagent` package and source identity. Evaluation imports use `mini_swe_rsi.evaluation.swebench` and do not depend on an external package with that name. The root `runtime/` directory contains only the container Dockerfile and dependency lock.

### Implementation entry points

- [online.py](../src/mini_swe_rsi/online.py): online scheduling, checks, and adoption.
- [single.py](../src/mini_swe_rsi/single.py): local single-task input, independent verification, and the single-task promotion store.
- [driver.py](../src/mini_swe_rsi/driver.py): container execution and candidate reruns.
- [broker.py](../src/mini_swe_rsi/broker.py): model service, usage accounting, and sessions.
- [model_protocol.py](../src/mini_swe_rsi/model_protocol.py): text action protocol, message normalization, and response adaptation.
- [model_identity.py](../src/mini_swe_rsi/model_identity.py): provider identity and capacity resolution.
- [online_checks.py](../src/mini_swe_rsi/online_checks.py): online identities, candidate snapshot validation, and acceptance rules.
- [roles.py](../src/mini_swe_rsi/workflow/roles.py): role definitions, capabilities, and prompt templates.
- [online_records.py](../src/mini_swe_rsi/online_records.py): run state, immutable decisions, and version installation.
- [candidate_jobs.py](../src/mini_swe_rsi/candidate_jobs.py): durable candidate-check registration and concurrent execution.
- [review_cache.py](../src/mini_swe_rsi/workflow/review_cache.py): committee cache bound to model, parent version, and source identity.
- [workflow.py](../src/mini_swe_rsi/workflow/workflow.py): the seven-step state machine and role operations.

The existing model, online-loop, and workflow entry points remain available. Dedicated modules own rule calculations, role definitions, and persistence. The existing state machines retain business orchestration and recovery decisions. Normal commits and recovery replay share state and version installation components; the Broker retains session authentication and authorization. Disk record fields are unchanged. `workflow_sha256` covers workflow, role, and committee cache implementations, and the new modules are included in the control manifest. Existing run directories still require the corresponding code version after source identities change.
