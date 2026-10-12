# Self-Improving Coding Agent runner
"""
An agent runner that handles benchmark evaluation and self-improvement cycles.
"""

import os
import re
import sys
import json
import shutil
import random
import logging
import asyncio
import platform
import argparse
import subprocess

from uuid import uuid4
from typing import Type
from pathlib import Path
from datetime import datetime
from asyncio.subprocess import Process

from base_agent.src.benchmarks import benchmark_registry
from base_agent.src.benchmarks.base import BaseBenchmark, BenchmarkTracker, Problem
from base_agent.src.utils.archive_analysis import (
    ArchiveAnalyzer,
    compute_statistics,
    ScoreType,
)
from base_agent.src.llm.api import create_completion
from base_agent.src.llm.base import Message
from base_agent.src.types.llm_types import Model, TextContent

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global locks
benchmark_trackers = {}
benchmark_locks = {}

# Utility functions ------------------------------------------------------------


def setup_argparse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Self referential agent")
    parser.add_argument(
        "--experiment-id", "-id", type=int, help="ID of experiment to resume"
    )
    parser.add_argument(
        "--iterations", "-n", type=int, default=20, help="Number of iterations to run"
    )
    parser.add_argument(
        "--workers", type=int, default=8, help="Number of parallel problem workers"
    )
    subparsers = parser.add_subparsers(dest="command", help="Sub-command to perform")

    # Just run through a single benchmark for testing
    test_parser = subparsers.add_parser(
        "test", help="Just run the benchmark on the latest agent iteration"
    )
    test_parser.add_argument("--name", default="gsm8k", help="Benchmark ID to run")

    return parser


def get_next_dir_number(base_dir: str | Path) -> int:
    """
    Returns the next free directory name of the form `run_{i}` in some base_dir.
    """
    base_path = Path(base_dir)
    numbered_dirs = []
    for path in base_path.iterdir():
        if path.is_dir():
            try:
                if path.name.startswith("run_"):
                    numbered_dirs.append(int(path.name.lstrip("run_")))
            except ValueError:
                continue
    # If no numbered directories exist, start with 1
    if not numbered_dirs:
        return 1
    return max(numbered_dirs) + 1


def select_base_agent(
    analyzer: ArchiveAnalyzer,
    current_iteration: int,
    score_type: ScoreType = "mean_score",
) -> int:
    """
    Select which previous agent iteration to use as the base for improvement.

    Args:
        analyzer: ArchiveAnalyzer instance
        current_iteration: The current iteration number
        score_type: Utility score or mean score

    Returns:
        The iteration number to use as base for improvement
    """
    # Get performance data with utility scores
    scores_df, summaries_df = analyzer.get_problem_scores_by_iteration()
    # print(scores_df, summaries_df)
    if scores_df.empty or summaries_df.empty:
        return 0  # Default to first agent if no data

    # Compute statistics including confidence intervals
    stats = compute_statistics(scores_df, summaries_df, score_type=score_type)
    # print(stats)
    if stats.empty:
        return 0

    # Find the best performing iteration
    # Mean score here corresponds to either utility_score or perf based on score_type
    best_idx = stats["target_score"].idxmax()
    best_stats = stats.loc[best_idx]

    # Get the lower confidence bound of the best performing agent
    best_lower_bound = best_stats["ci_lower"]

    # Check each iteration from current back to best (or 0), looking for first
    # agent that meets our criteria
    for i in range(current_iteration, -1, -1):
        if i not in stats.index:
            continue

        current_mean = stats.loc[i, "target_score"]
        logger.info(
            f"Agent {i} mean {score_type} score: {current_mean}; best lower bound: {best_lower_bound}"
        )
        if current_mean >= best_lower_bound:
            return i

        # If we've gone past the best iteration and haven't found a suitable
        # agent, use the best
        try:
            if i <= best_idx:
                return best_idx
        except Exception as e:
            logger.warning(e)
            continue

    logger.info("Defaulting to base agent iteration 0")
    return 0  # Fallback to first agent if something goes wrong


async def run_docker_command(*args) -> tuple[bool, str, str]:
    """Run a command inside a docker container"""
    logger.debug("Running docker command:")
    logger.debug(" ".join(args))
    proc: Process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    success = True
    if proc.returncode != 0:
        logger.debug(f"Command {args[0]} failed: {stderr.decode()}")
        success = False
    return success, stdout.decode().strip(), stderr.decode().strip()


async def wait_for_container_ready(container_name: str, timeout: float = 30):
    """Waits until a container is ready after starting it up"""
    start_time = asyncio.get_event_loop().time()
    while (asyncio.get_event_loop().time() - start_time) < timeout:
        try:
            # Get container status in JSON format
            _, stdout, _ = await run_docker_command(
                "docker",
                "inspect",
                "--format",
                "{{json .State.Status}}",
                container_name,
            )
            status = json.loads(stdout)

            if status == "running":
                # Additional health check - try a basic command
                try:
                    await run_docker_command("docker", "exec", container_name, "ps")
                    return  # Container is truly ready
                except Exception:
                    pass  # Container not quite ready yet

        except Exception:
            pass  # Container might not exist yet

        await asyncio.sleep(0.1)  # Short delay before retry

    raise TimeoutError(
        f"Container {container_name} did not become ready within {timeout} seconds"
    )


def load_metadata(exp_dir: Path) -> dict:
    """Load the experiment metadata, creating if doesn't exist"""
    metadata_file = exp_dir / "metadata.json"
    if not metadata_file.exists():
        metadata = {
            "experiment_start_timestamp": datetime.now().isoformat(),
            "python_version": sys.version,
            "executable": sys.executable,
            "agent_iteration": 0,
            "git_commit": None,
            # NOTE: the current_benchmark_idx field will be updated later, and
            # is set to -1 for improvement tasks, and 0, 1, 2, ... otherwise.
            # "current_benchmark_idx": None,
        }
        try:
            metadata["git_commit"] = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip()
        except subprocess.CalledProcessError:
            pass

        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)
    else:
        with open(metadata_file) as f:
            metadata = json.load(f)
    return metadata


def update_metadata(exp_dir: Path, **kwargs) -> None:
    """Update specific metadata fields"""
    metadata = load_metadata(exp_dir)
    metadata.update(kwargs)
    with open(exp_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)


async def update_benchmark_progress(
    exp_dir: Path, benchmark_name: str, problem_id: str, total_problems: int
) -> None:
    """Update the metadata to mark a problem as completed"""
    metadata_file = exp_dir / "metadata.json"

    # Use a file lock to ensure thread safety
    async with asyncio.Lock():
        with open(metadata_file) as f:
            metadata = json.load(f)

        # Initialize benchmark progress if not exists
        if "benchmark_progress" not in metadata:
            metadata["benchmark_progress"] = {}

        if benchmark_name not in metadata["benchmark_progress"]:
            metadata["benchmark_progress"][benchmark_name] = {
                "total": total_problems,
                "completed": 0,
                "problems_completed": [],
            }

        # Ensure total is up-to-date
        metadata["benchmark_progress"][benchmark_name]["total"] = total_problems

        # Update the progress
        if (
            problem_id
            not in metadata["benchmark_progress"][benchmark_name]["problems_completed"]
        ):
            metadata["benchmark_progress"][benchmark_name]["problems_completed"].append(
                problem_id
            )
            metadata["benchmark_progress"][benchmark_name]["completed"] += 1

        # Save metadata
        with open(metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)


async def generate_contextual_summary(
    problem_statement: str,
    llm_answer: str,
    trace: str,
    score: float,
    parse_errors: str | None = None,
    answer_discussion: str | None = None,
) -> str:
    """
    Generates a summary of the long agent trace for archival and indexing purposes.

    Incorporates the original problem statement, the agent's final answer,
    score, any parsing errors and answer discussion to help better
    contextualise the trace summary.
    """
    scoring_context = (
        f"The agent's answer got a score of {score}"
        "(For binary correct/incorrect answers 0 represents a wrong answer and 1 represents a correct answer. "
        "For other problem types, a higher score is better.)"
    )

    if parse_errors:
        scoring_context += f"\nThere were issues parsing the answer: {parse_errors}"

    if answer_discussion:
        scoring_context += f"\nHere is some additional information about the answer to this problem:\n{answer_discussion}\n"

    summary_prompt = f"""Below is a trace of an agent's execution on the following problem:

Problem:
{problem_statement}

Agent's Answer:
{llm_answer}

Execution Result:
{scoring_context}

Trace:
{trace}

Please write a critical analysis of the agent's performance, taking into account both the solution process and the final outcome. Consider:
- Did the agent follow a logical approach?
- Were there unnecessary steps or inefficiencies?
- If the answer was wrong, where did the agent's reasoning fail?
- If there were parsing errors, what caused them?
- What specific improvements could make the agent more effective?

Keep your analysis concise (no more than 1-2 paragraphs max) but thorough.
"""

    summary = await create_completion(
        messages=[
            Message(
                role="system",
                content=[
                    TextContent(
                        text="You are a critical yet constructive and creative evaluator of AI agent performance."
                    )
                ],
            ),
            Message(role="user", content=[TextContent(text=summary_prompt)]),
        ],
        model=Model.GEMINI_FLASH_2,
    )

    # Assume we'll get a single text completion
    return summary.content[0].text


# New Job class for the queue -------------------------------------------------
class Job:
    """Represents a benchmark problem to be processed"""

    def __init__(self, benchmark_name: str, problem: Problem, benchmark: BaseBenchmark):
        self.benchmark_name = benchmark_name
        self.problem = problem
        self.benchmark = benchmark
        self.id = f"{benchmark_name}:{problem.problem_id}"

    def __str__(self):
        return self.id


# Process a single job --------------------------------------------------------
async def process_job(
    exp_dir: Path,
    agent_dir: Path,
    job: Job,
    worker_id: int,
) -> bool:
    """Process a single benchmark problem job"""
    TIMEOUT_SECONDS = 10 * 60  # 10 minutes timeout
    COST_THRESHOLD_USD = 3.00  # 3 USD cost limit per problem

    benchmark = job.benchmark
    problem = job.problem

    benchmark_dir = agent_dir / "benchmarks" / benchmark.name
    benchmark_dir.mkdir(parents=True, exist_ok=True)

    benchmark_lock = benchmark_locks[job.benchmark_name]
    results_tracker = benchmark_trackers[job.benchmark_name]

    # Check if the problem is already completed
    async with benchmark_lock:
        if problem.problem_id in results_tracker.results:
            if results_tracker.results[problem.problem_id].is_complete():
                logger.info(f"Problem {problem.problem_id} already completed, skipping")
                return True
            else:
                logger.info(f"Re-doing incomplete problem {problem.problem_id}")
        else:
            results_tracker.start_problem(problem.problem_id)

    logger.info(f"Starting {problem.problem_id} (worker {worker_id})")

    logdir = agent_dir / "benchmarks" / benchmark.name / "traces" / problem.problem_id

    # Create problem data directory
    dirname = re.sub(r"[^a-zA-Z0-9_]", "", f"{benchmark.name}_{problem.problem_id}")
    problem_data_dir = exp_dir / "problem_data" / dirname
    if problem_data_dir.exists():
        shutil.rmtree(problem_data_dir)
    problem_data_dir.mkdir(parents=True)

    timed_out = False
    cost_threshold_exceeded = False

    # Create unique container name
    local_id = f"agent_{uuid4().hex[:8]}"
    container_name = f"sica_{benchmark.name}_{problem.problem_id}_{local_id}"
    container_name = re.sub(f"[^a-zA-Z0-9_]", "", container_name)

    try:
        # Ensure answer directory exists
        logdir.mkdir(parents=True, exist_ok=True)

        # Start the container
        container_cmd = [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container_name,
        ]
        container_cmd += ["-p", f"808{worker_id}:8080"]
        # Mount the experiment directory (results/run_{i})
        container_cmd += [
            "-v",
            f"{exp_dir.absolute()}:/home/agent/{exp_dir}:rw",
        ]
        # Mount the problem data directory to the workdir
        container_cmd += [
            "-v",
            f"{problem_data_dir.absolute()}:/home/agent/workdir:rw",
        ]
        container_cmd += ["sica_sandbox", "tail", "-f", "/dev/null"]
        success, _, stderr = await run_docker_command(*container_cmd)
        if not success:
            logger.error(
                f"Failed to start container for {problem.problem_id}: {stderr}"
            )
            return False

        await wait_for_container_ready(container_name)

        try:
            # Setup the problem environment
            await benchmark.setup_problem(problem, problem_data_dir, container_name)

            # Write content directly using echo
            container_prompt_file = "/tmp/prompt.txt"

            # Create the file in the container with explicit error checking
            create_file_cmd = [
                "docker",
                "exec",
                "-u",
                "agent:agent",
                container_name,
                "bash",
                "-c",
                f"cat > {container_prompt_file} << 'EOF'\n{problem.statement}\nEOF",
            ]
            success, stdout, stderr = await run_docker_command(*create_file_cmd)
            if not success:
                logger.error(f"Failed to create prompt file: {stderr}")

            await asyncio.sleep(
                random.random() * 2
            )  # stagger the start of the agents to avoid API rate limit issues

            # Execute the agent command and get stdout
            agent_module = str(agent_dir / "agent_code").replace("/", ".")
            agent_cmd = ["python", "-m", agent_module, "benchmark"]
            agent_cmd += ["--prompt-file", container_prompt_file]
            agent_cmd += ["--logdir", str(logdir)]
            agent_cmd += ["--timeout", str(TIMEOUT_SECONDS)]
            agent_cmd += ["--cost-threshold", str(COST_THRESHOLD_USD)]

            try:
                # An additional safety feature; if timeout cancellation
                # doesn't happen at the application layer, we cancel
                # the docker container
                async with asyncio.timeout(TIMEOUT_SECONDS + 10):
                    success, stdout, stderr = await run_docker_command(
                        "docker", "exec", container_name, *agent_cmd
                    )
            except asyncio.TimeoutError:
                # Timeout exceeded the grace period
                logger.warning(
                    f"Problem {problem.problem_id} timed out after {TIMEOUT_SECONDS} seconds"
                )
                timed_out = True
                success = False
                stderr = f"Execution timed out after {TIMEOUT_SECONDS} seconds"

                # Write timeout information to trace file
                timeout_msg = (
                    f"\n\nEXECUTION TIMED OUT AFTER {TIMEOUT_SECONDS} SECONDS\n"
                )
                trace_path = logdir / "trace.txt"
                if trace_path.exists():
                    with open(trace_path, "a") as f:
                        f.write(timeout_msg)
                else:
                    trace_path.write_text(timeout_msg)

            if not success:
                logger.error(f"Problem {problem.problem_id} failed: {stderr}")
                return False

            if not timed_out:
                tokens, cached, cost, time = stdout.splitlines()[-1].split("|")
                percent_cached = (
                    int(cached) / int(tokens) * 100 if int(tokens) > 0 else 0
                )

                if float(cost) >= COST_THRESHOLD_USD:
                    cost_threshold_exceeded = True

                if float(time) >= TIMEOUT_SECONDS - 0.5:
                    timed_out = True
                    logger.info(
                        f"Execution of {problem.problem_id} gracefully timed out after {TIMEOUT_SECONDS} seconds"
                    )

                logger.info(
                    f"{problem.problem_id} | tokens: {tokens} (cached: {percent_cached:.2f}%), cost: ${float(cost):.4f}, duration: {float(time):.2f}s"
                )
            else:
                # Use placeholder values for timeout cases that exceed grace period
                tokens, cached, cost, time = 0, 0, 0, str(TIMEOUT_SECONDS)

            # Score the answer
            score, parse_errors, answer_discussion = await benchmark.score_problem(
                problem,
                str(problem_data_dir.absolute()),
                str(logdir.absolute()),
                container_name,
            )

            score_path = logdir / "score.txt"
            score_path.write_text(str(score))

            async with benchmark_lock:
                results_tracker.update_problem(
                    problem.problem_id,
                    tokens_used=int(tokens),
                    num_cached_tokens=int(cached),
                    cost_estimate=float(cost),
                    wall_time=float(time),
                    score=float(score),
                    timed_out=timed_out,
                    cost_threshold_exceeded=cost_threshold_exceeded,
                )

            answer_path = logdir / "answer.txt"
            if answer_path.exists():
                llm_answer = answer_path.read_text()
            else:
                llm_answer = "No LLM answer provided"
            trace_path = logdir / "trace.txt"
            if trace_path.exists():
                trace = trace_path.read_text()
            else:
                trace = "No trace was available"

            # Add timeout information to the summary context
            timeout_context = ""
            if timed_out:
                timeout_context = f"\nNOTE: This execution was terminated after reaching the {TIMEOUT_SECONDS} second time budget limit."

            summary = await generate_contextual_summary(
                problem.statement,
                llm_answer,
                trace,
                score,
                parse_errors,
                (
                    answer_discussion + timeout_context
                    if answer_discussion
                    else timeout_context
                ),
            )

            summary_path = logdir / "summary.txt"
            summary_path.write_text(summary)

            # Update the benchmark progress in metadata
            await update_benchmark_progress(
                exp_dir, benchmark.name, problem.problem_id, len(benchmark.problems)
            )

            return True

        except Exception as e:
            logger.error(f"Error processing problem {problem.problem_id}: {e}")
            return False
    finally:
        # Cleanup running docker container
        await run_docker_command("docker", "rm", "-f", container_name)

        # Cleanup problem data directory
        shutil.rmtree(problem_data_dir)
        logger.info(f"Completed {problem.problem_id}")


async def generate_benchmark_statistics(agent_dir: Path, benchmark_name: str) -> None:
    """Generate and save benchmark statistics"""
    benchmark_dir = agent_dir / "benchmarks" / benchmark_name
    results_tracker = BenchmarkTracker(benchmark_dir / "results.jsonl")

    # Aggregate scores and metrics across all problems to calculate overall metrics
    scores = []
    tokens, cached, cost, time = 0, 0, 0, 0
    for result in results_tracker.results.values():
        if result.score is not None:
            scores.append(result.score)
        else:
            # Default invalid score to 0
            scores.append(0)
        if result.tokens_used:
            tokens += result.tokens_used
        if result.num_cached_tokens:
            cached += result.num_cached_tokens
        if result.cost_estimate:
            cost += result.cost_estimate
        if result.wall_time:
            time += result.wall_time

    avg_score = sum(scores) / len(scores) if len(scores) > 0 else 0
    aggregates = dict(
        avg_score=avg_score,
        tokens=tokens,
        num_cached_tokens=cached,
        cost=cost,
        time=time,
    )
    with open(benchmark_dir / "perf.json", "w") as f:
        f.write(json.dumps(aggregates))

    logger.info(
        f"Generated performance statistics for {benchmark_name}: avg_score={avg_score:.4f}"
    )


# Run benchmarks with job queue -----------------------------------------------
async def run_benchmarks_with_job_queue(
    exp_dir: Path,
    agent_dir: Path,
    benchmarks: list[Type[BaseBenchmark]],
    max_workers: int = 6,
) -> None:
    """Run multiple benchmarks concurrently using a job queue approach"""

    # Create worker ID queue (used for port assignment)
    id_queue = asyncio.Queue()
    for i in range(max_workers):
        id_queue.put_nowait(i)

    # Job queue
    job_queue = asyncio.Queue()

    # Load benchmark progress from metadata
    metadata = load_metadata(exp_dir)
    benchmark_progress = metadata.get("benchmark_progress", {})

    # Initialize benchmarks and enqueue jobs
    benchmarks_dict = {}

    for benchmark_cls in benchmarks:
        benchmark = benchmark_cls(seed=1, subset_size=18)
        # benchmark = benchmark_cls(seed=1, subset_size=None)  # For full benchmarks
        logger.info(f"Initializing benchmark: {benchmark.name}")

        benchmark_dir = agent_dir / "benchmarks" / benchmark.name
        benchmark_dir.mkdir(parents=True, exist_ok=True)

        # Create a global results tracker and lock for this benchmark
        benchmark_locks[benchmark.name] = asyncio.Lock()
        benchmark_trackers[benchmark.name] = BenchmarkTracker(
            benchmark_dir / "results.jsonl"
        )

        # Get previously completed problems
        completed_problems = set(
            benchmark_progress.get(benchmark.name, {}).get("problems_completed", [])
        )

        # Add problems to the queue
        problems_to_process = 0
        for problem in benchmark.problems:
            if problem.problem_id not in completed_problems:
                job = Job(benchmark.name, problem, benchmark)
                await job_queue.put(job)
                problems_to_process += 1

        logger.info(
            f"Added {problems_to_process} problems from {benchmark.name} to queue"
        )
        benchmarks_dict[benchmark.name] = benchmark

    # If no jobs to process, we're done
    if job_queue.empty():
        logger.info("No problems to process, all benchmarks are complete")
        # Generate final perf reports for each benchmark
        for benchmark_name, benchmark in benchmarks_dict.items():
            await generate_benchmark_statistics(agent_dir, benchmark_name)
        return

    async def worker():
        worker_id = await id_queue.get()
        try:
            while True:
                try:
                    job = await job_queue.get()
                except asyncio.QueueEmpty:
                    # No more jobs
                    break

                try:
                    # Process the job
                    logger.info(f"Worker {worker_id} processing job {job}")
                    await process_job(exp_dir, agent_dir, job, worker_id)
                except Exception as e:
                    logger.error(f"Error in worker {worker_id} processing {job}: {e}")
                finally:
                    job_queue.task_done()
        finally:
            id_queue.put_nowait(worker_id)

    # Start workers
    logger.info(f"Starting {max_workers} workers")
    workers = []
    for _ in range(max_workers):
        task = asyncio.create_task(worker())
        workers.append(task)

    # Wait for all jobs to complete
    await job_queue.join()

    # Cancel workers
    for task in workers:
        task.cancel()

    # Wait for workers to be cancelled
    await asyncio.gather(*workers, return_exceptions=True)

    # Generate final perf reports for each benchmark
    for benchmark_name, benchmark in benchmarks_dict.items():
        await generate_benchmark_statistics(agent_dir, benchmark_name)


async def run_meta_agent_benchmark(
    exp_id: int,
    iteration: int,
    exp_dir: Path,
    current_dir: Path,
    next_dir: Path,
    self_referential: bool = True,
) -> None:
    """Create the next agent version based on the current agent's results

    We can conceptually think of this as a 'meta agent improvement' benchmark.
    """
    current_agent_code_dir = current_dir / "agent_code"
    next_agent_code_dir = next_dir / "agent_code"
    logger.info("Creating new agent")
    logger.info(f"current: {current_agent_code_dir}")
    logger.info(f"next: {next_agent_code_dir}")

    # 1. Select the base agent to use for improvement
    aa = ArchiveAnalyzer(f"results/run_{exp_id}")
    base_iter = select_base_agent(aa, iteration, score_type="mean_score")
    base_agent_dir = f"results/run_{exp_id}/agent_{base_iter}/agent_code"
    logger.info(f"Selected agent {base_iter} as base for improvement")

    # 2. Copy the selected agent's code as starting point
    base_code_dir = Path(base_agent_dir)
    shutil.copytree(base_code_dir, next_agent_code_dir)

    try:
        best_iter = aa.get_best_agent_iteration()
    except Exception:
        best_iter = 0

    # Old directory copying strategy (left in for reference)
    # best_agent_dir = f"results/run_{exp_id}/agent_{best_iter}/agent_code"
    # print(f"Detected best code dir as: {best_agent_dir}")
    # base_strategy: Literal["best", "latest"] = "best"
    # if base_strategy == "best":
    #     base_code_dir = Path(best_agent_dir)
    # else:
    #     # Use the current agent's code directory for improvement
    #     base_code_dir = current_agent_code_dir
    #     print(f"Using current agent code dir as: {current_agent_code_dir}")
    # print(f"Next agent code dir is: {next_agent_code_dir}")
    # Copy the current agent code as a starting point
    # shutil.copytree(base_code_dir, next_agent_code_dir)

    # 3. Always copy the change log from the current agent to maintain history
    try:
        shutil.copy(
            current_agent_code_dir / "agent_change_log.md",
            next_agent_code_dir / "agent_change_log.md",
        )
    except shutil.SameFileError:
        pass

    # Setup meta-improvement logs directory
    current_meta_log_dir = current_dir / "meta_improvement_logs"
    container_meta_log_path = f"/home/agent/meta_improvement_logs_{iteration}"
    current_meta_log_dir.mkdir(parents=True, exist_ok=True)

    # if self_referential:
    #     agent_module = str(best_agent_dir).replace("/", ".")
    #     # Use current (latest) agent as meta agent
    #     # agent_module = str(current_agent_code_dir).replace("/", ".")
    # else:
    #     # NOTE: this could be a specialised meta-improvement agent
    #     # agent_module = "base_agent.agent_code"
    #     agent_module = "meta_agent"

    # Create a unique container name
    local_id = f"agent_{uuid4().hex[:8]}"
    container_name = f"sica_run_{exp_id}_agent_{iteration}_improvement_{local_id}"
    container_name = re.sub(f"[^a-zA-Z0-9_]", "", container_name)

    try:
        # Start the container
        container_cmd = ["docker", "run", "--rm", "-d", "--name", container_name]
        # Map port local:remote for monitoring server
        container_cmd += ["-p", "8080:8080"]
        # Mount the full agent archive as read-only
        container_cmd += ["-v", f"{exp_dir.absolute()}:/home/agent/archive:ro"]
        # Mount the new agent directory as the workdir
        container_cmd += [
            "-v",
            f"{next_agent_code_dir.absolute()}:/home/agent/workdir:rw",
        ]
        # Mount the meta agent directory
        # container_cmd += [
        #     "-v",
        #     f"{Path('.').absolute()}/meta_agent:/home/agent/meta_agent:ro",
        # ]
        # Mount the env file for secrets
        # container_cmd += ["-v", f"{Path('.env').absolute()}:/home/agent/.env:ro"]
        # Mount meta agent log directory
        container_cmd += [
            "-v",
            f"{current_meta_log_dir .absolute()}:{container_meta_log_path}:rw",
        ]
        container_cmd += ["sica_sandbox", "tail", "-f", "/dev/null"]
        success, _, stderr = await run_docker_command(*container_cmd)
        if not success:
            logger.error(f"Could not start container: {stderr}")
            return

        await wait_for_container_ready(container_name)

        # Execute the (meta) agent command and get stdout
        # archive.agent_{i}.agent_code
        agent_module = f"archive.agent_{best_iter}.agent_code"
        # agent_module = f"meta_agent"
        agent_cmd = ["python", "-m", agent_module, "improve"]
        agent_cmd += ["--workdir", "/home/agent/workdir"]
        # agent_cmd += ["--logdir", f"/home/agent/archive/agent_{iteration}/meta_improvement"]
        agent_cmd += ["--logdir", container_meta_log_path]
        agent_cmd += ["--best-iter", str(best_iter)]
        agent_cmd += ["--current-iter", str(iteration)]
        success, stdout, stderr = await run_docker_command(
            "docker", "exec", container_name, *agent_cmd
        )

        if not success:
            logger.error(f"Meta agent failed at iteration {iteration}: {stderr}")
            logger.error(stdout)
            return

        tokens, cached, cost, time = stdout.splitlines()[-1].split("|")
        percent_cached = int(cached) / int(tokens) * 100 if int(tokens) > 0 else 0
        logger.info(
            f"meta-improvement || tokens: {tokens} (cached: {percent_cached:.2f}%), cost: ${float(cost):.4f}, duration: {float(time):.2f}s"
        )

        # 4. Generate and save a summary for the meta-improvement logs
        trace_path = current_meta_log_dir / "execution_tree.txt"
        if trace_path.exists():
            trace = trace_path.read_text()
        else:
            trace = "No trace was available"

        # Define a simple problem statement and answer for context
        problem_statement = f"Improve the agent from iteration {base_iter} to create iteration {iteration + 1}"
        llm_answer = "Agent code updated" if success else "Improvement failed"
        score = 1.0 if success else 0.0  # Binary success/failure

        # Additional context for the summary
        meta_context = (
            f"The 'answer' actually pertains to whether the agent was successful (1) or errored out (0) during the meta-improvement task"
            f"Meta-improvement process used {tokens} tokens ({percent_cached:.2f}% cached), "
            f"cost ${float(cost):.4f}, and took {float(time):.2f} seconds."
        )
        if stderr:
            meta_context += f"\nErrors encountered: {stderr}"

        summary = await generate_contextual_summary(
            problem_statement=problem_statement,
            llm_answer=llm_answer,
            trace=trace,
            score=score,
            parse_errors=None,
            answer_discussion=meta_context,
        )

        summary_path = current_meta_log_dir / "summary.txt"
        summary_path.write_text(summary)
        logger.info(f"Meta-improvement summary saved to {summary_path}")

        # Generate the new agent's documentation
        # docs_cmd = ["python", "/home/agent/workdir/utils/documentation.py"]
        # docs_cmd += ["/home/agent/workdir", ">"]
        # docs_cmd += ["/home/agent/workdir/DOCUMENTATION.md"]
        # success, stdout, stderr = await run_docker_command(
        #     "docker", "exec", container_name, *docs_cmd
        # )
        # if not success:
        #     logger.warning(f"Failed to re-generate docs {iteration}: {stderr}")

        # Copy the meta-improvement logs into the archive
        # NOTE: this is already done by mounting the appropriate dir
        # meta_log_cp_cmd = ["docker", "cp", f"{container_name}:{container_meta_log_path}"]
        # meta_log_cp_cmd += [current_dir / "meta_improvement_logs"]
        # await run_docker_command(*meta_log_cp_cmd)

        # At this point, re-build the sica_sandbox image in case the
        # requirements.txt changed; ready for the next iteration

        target_arch = "x86_64"  # Default architecture
        # Detect if the script is running on a Mac with Apple Silicon
        if platform.system() == "Darwin" and platform.processor() == "arm":
            target_arch = "aarch64"
        build_cmd = ["docker", "buildx", "build"]
        build_cmd += ["--build-context", "base_agent=./base_agent"]
        build_cmd += ["--build-arg", f"TARGET_ARCH={target_arch}"]
        build_cmd += [
            "--build-arg",
            f"ANTHROPIC_API_KEY={os.environ.get('ANTHROPIC_API_KEY')}",
        ]
        build_cmd += [
            "--build-arg",
            f"OPENAI_API_KEY={os.environ.get('OPENAI_API_KEY')}",
        ]
        build_cmd += [
            "--build-arg",
            f"FIREWORKS_AI_API_KEY={os.environ.get('FIREWORKS_AI_API_KEY')}",
        ]
        build_cmd += [
            "--build-arg",
            f"GEMINI_API_KEY={os.environ.get('GEMINI_API_KEY')}",
        ]
        build_cmd += [
            "--build-arg",
            f"DEEPSEEK_API_KEY={os.environ.get('DEEPSEEK_API_KEY')}",
        ]
        build_cmd += [
            "--build-arg",
            f"VERTEX_PROJECT_ID={os.environ.get('VERTEX_PROJECT_ID')}",
        ]
        build_cmd += ["-f", "sandbox/Dockerfile", "-t", "sica_sandbox", "sandbox"]

        await run_docker_command(*build_cmd)

    finally:

        # Cleanup running docker container
        await run_docker_command("docker", "rm", "-f", container_name)


async def main():
    """Main entry point for the agent runner"""
    parser = setup_argparse()
    args = parser.parse_args()
    logger.debug(f"Setup parser: {args}")

    # Either get the specified experiment id, or get the next one
    exp_id = args.experiment_id or get_next_dir_number("results")
    exp_dir = Path("results") / f"run_{exp_id}"

    # Setup experiment directory
    if not exp_dir.exists():
        exp_dir.mkdir(parents=True)
        load_metadata(exp_dir)  # Creates if doesn't exist

        # Copy the initial agent
        agent_0_dir = exp_dir / "agent_0"
        agent_0_dir.mkdir()
        shutil.copytree("base_agent", agent_0_dir / "agent_code")
        logger.info(f"Initialized experiment {exp_id} in {exp_dir}")

    # Handle test mode
    if args.command == "test":
        # Find the latest agent iteration
        latest_agent = max(
            (d for d in exp_dir.iterdir() if d.name.startswith("agent_")),
            key=lambda d: int(d.name.split("_")[1]),
        )

        # Get the benchmark class
        benchmark_cls = benchmark_registry.get(args.name)
        if not benchmark_cls:
            logger.error(f"Benchmark {args.name} not found")
            return

        # Run the specified benchmark using the job queue approach
        await run_benchmarks_with_job_queue(
            exp_dir, latest_agent, [benchmark_cls], args.workers
        )
        return

    # Load state for resumption
    metadata = load_metadata(exp_dir)
    start_iteration = metadata["agent_iteration"]
    logger.info(f"Starting from iteration {start_iteration}")

    # Full loop
    for i in range(start_iteration, args.iterations):
        current_agent_dir = exp_dir / f"agent_{i}"
        next_agent_dir = exp_dir / f"agent_{i+1}"

        # Run all benchmarks concurrently using job queue
        logger.info(f"Running benchmarks for iteration {i}")
        await run_benchmarks_with_job_queue(
            exp_dir, current_agent_dir, list(benchmark_registry.values()), args.workers
        )

        # Improvement task
        logger.info(f"Starting agent improvement for iteration {i}")
        next_agent_dir.mkdir(exist_ok=False, parents=True)
        await run_meta_agent_benchmark(
            exp_id, i, exp_dir, current_agent_dir, next_agent_dir
        )

        # Update metadata for next iteration
        update_metadata(exp_dir, agent_iteration=i + 1)
        logger.info(f"Completed iteration {i}, moving to {i+1}")

        # Important: reset benchmark progress for the new iteration
        update_metadata(exp_dir, benchmark_progress=dict())
        logger.info("Reset benchmark progress for next iteration")


if __name__ == "__main__":
    asyncio.run(main())
