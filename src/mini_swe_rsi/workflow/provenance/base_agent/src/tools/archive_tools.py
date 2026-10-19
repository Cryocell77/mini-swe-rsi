# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""
Some tools to expose the archive analysis functions to the ArchiveExplorer
agent function.

TODO: update these examples with more realistic tool results.
"""
import logging

from typing import List
from pydantic import Field

from .base_tool import BaseTool
from ..utils.archive_analysis import ArchiveAnalyzer
from ..types.tool_types import ToolResult
from ..types.agent_types import AgentInterface

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class AnalyzeRun(BaseTool):
    TOOL_NAME = "analyze_run"
    TOOL_DESCRIPTION = """A tool to perform a basic analysis of the archive."""

    def __init__(self, calling_agent: AgentInterface, **data):
        super().__init__(calling_agent=calling_agent, **data)

    async def run(self) -> ToolResult:
        aa = ArchiveAnalyzer("/home/agent/archive")
        try:
            result = aa.analyze_run_formatted()
            return ToolResult(
                tool_name=self.TOOL_NAME, success=True, output=str(result)
            )
        except Exception as e:
            return ToolResult(tool_name=self.TOOL_NAME, success=False, errors=str(e))

    @classmethod
    def generate_examples(cls) -> list[tuple["AnalyzeRun", ToolResult]]:
        from ..agents.implementations import DemoAgent

        return [
            (
                cls(
                    calling_agent=DemoAgent(),
                ),
                ToolResult(
                    tool_name=cls.TOOL_NAME,
                    success=True,
                    output="""
|   Iteration |   gsm8k |    math |   Utility | Avg Cost   | Avg Time   |   Avg Tokens |
|------------:|--------:|--------:|----------:|:-----------|:-----------|-------------:|
|       0.000 |   0.940 |   0.000 |     0.551 | $3.51      | 603.6s     |  1060881.500 |
|       1.000 |   0.980 | nan     |     0.901 | $3.95      | 608.0s     |  1216798.000 |
""",
                ),
            ),
        ]


class WorstProblems(BaseTool):
    TOOL_NAME = "worst_problems"
    TOOL_DESCRIPTION = (
        """A tool to retrieve the worst performing problems and their analyses"""
    )

    agent_iteration: int = Field(
        ...,
        description="The agent iteration for which you'd like to find the worst performing problems",
    )
    count: int = Field(
        default=3,
        description="The number of problem instances to return, sorted by score",
        lt=10,
    )
    benchmark_name: str = Field(
        default="all", description="The benchmark to filter on, or 'all' to not filter on anything"
    )

    def __init__(self, calling_agent: AgentInterface, **data):
        super().__init__(calling_agent=calling_agent, **data)

    async def run(self) -> ToolResult:
        aa = ArchiveAnalyzer("/home/agent/archive")
        try:
            bench_name = self.benchmark_name if self.benchmark_name.strip() != "all" else None
            result = aa.get_worst_performing_problems_formatted(
                self.agent_iteration, self.count, bench_name
            )
            return ToolResult(
                tool_name=self.TOOL_NAME, success=True, output=str(result)
            )
        except Exception as e:
            return ToolResult(tool_name=self.TOOL_NAME, success=False, errors=str(e))

    @classmethod
    def generate_examples(cls) -> list[tuple["WorstProblems", ToolResult]]:
        from ..agents.implementations import DemoAgent

        return [
            (
                cls(calling_agent=DemoAgent(), agent_iteration=3, count=1),
                ToolResult(
                    tool_name=cls.TOOL_NAME,
                    success=True,
                    output="""
Worst 1 performing problems for iteration 3 from all benchmarks:

Benchmark: gsm8k - Problem 37
Score: 0.000
Resources: 25606 tokens, $0.083, 13.4s
Execution Tree:
No trace available
Summary:
The agent followed a logical and systematic approach to solving the problem by first calculating the total money from Lego sales ($195), then the cost of video games ($160), and verifying the remaining amount ($35) matched the given $5. However, there was a critical oversight in the verification step - the agent noted that $195 - $160 = $35, which doesn't match the stated $5 remaining in the problem, but failed to recognize this discrepancy as a red flag. This should have prompted the agent to double-check its calculations or question the problem's consistency. Despite this mathematical inconsistency, the agent's final answer of 0 Lego sets was technically correct since the problem stated John "plans to sell all his toys," but the agent reached this conclusion based on the problem statement rather than the mathematical validation.
""",
                ),
            ),
        ]


class BestProblems(BaseTool):
    TOOL_NAME = "best_problems"
    TOOL_DESCRIPTION = (
        """A tool to retrieve the best performing problems and their analyses"""
    )

    agent_iteration: int = Field(
        ...,
        description="The agent iteration for which you'd like to find the best performing problems",
    )
    count: int = Field(
        default=3,
        description="The number of problem instances to return, sorted by score",
        lt=10,
    )
    benchmark_name: str = Field(
        default="all", description="The benchmark to filter on, or 'all' to not filter on anything"
    )

    def __init__(self, calling_agent: AgentInterface, **data):
        super().__init__(calling_agent=calling_agent, **data)

    async def run(self) -> ToolResult:
        aa = ArchiveAnalyzer("/home/agent/archive")
        try:
            bench_name = self.benchmark_name if self.benchmark_name.strip() != "all" else None
            result = aa.get_best_performing_problems_formatted(
                self.agent_iteration, self.count, bench_name
            )
            return ToolResult(
                tool_name=self.TOOL_NAME, success=True, output=str(result)
            )
        except Exception as e:
            return ToolResult(tool_name=self.TOOL_NAME, success=False, errors=str(e))

    @classmethod
    def generate_examples(cls) -> list[tuple["BestProblems", ToolResult]]:
        from ..agents.implementations import DemoAgent

        return [
            (
                cls(calling_agent=DemoAgent(), agent_iteration=3, count=2),
                ToolResult(
                    tool_name=cls.TOOL_NAME,
                    success=True,
                    output="""
Best 2 performing problems for iteration 3 from all benchmarks:

Benchmark: gsm8k - Problem 0
Score: 1.000
Resources: 20498 tokens, $0.067, 10.1s
Summary: The agent performed efficiently and accurately, following a clear logical progression to solve the problem. The approach was structured in three main steps: identifying key information, calculating remaining eggs (16 - 7 = 9), and determining the final dollar amount (9 * $2 = $18). The agent used the calculate tool appropriately for each mathematical operation and submitted the answer in the correct format, leading to a successful solution. The entire process took just over 10 seconds and used minimal computational resources.

There weren't any unnecessary steps or inefficiencies in the solution process. The agent's work was methodical, well-documented, and each calculation was clearly explained with appropriate reasoning. The only potential optimization might be combining the two calculations into a single operation ((16 - 7) * 2), but the separate steps actually made the solution more readable and easier to verify. The final answer was correctly formatted as a plain number without currency symbols or additional text, showing good awareness of the submission requirements.

Benchmark: gsm8k - Problem 1
Score: 1.000
Resources: 14862 tokens, $0.048, 6.9s
Summary: The agent demonstrated a clear, logical, and efficient approach to solving this simple arithmetic problem. It broke down the problem into discrete steps: identifying the known values (2 bolts of blue fiber), calculating the white fiber amount (half of blue = 1 bolt), and then adding them together for the total (3 bolts). The agent used the calculate tool appropriately to perform the addition, though this was arguably unnecessary for such a simple calculation (2 + 1). The answer was submitted correctly using the submit_answer tool with clear reasoning, and the correct answer of 3 bolts was accepted.

While the agent's performance was satisfactory, the one minor optimization would be eliminating the unnecessary calculate tool call for such a basic addition. This would have made the solution even more efficient without compromising accuracy. However, this is a very minor critique given that the overall execution was clean, well-reasoned, and produced the correct result in under 7 seconds.
""",
                ),
            ),
        ]


class CompareIterations(BaseTool):
    TOOL_NAME = "compare_agent_iterations"
    TOOL_DESCRIPTION = """A tool to retrieve more detailed benchmark statistics for specific agent iterations"""

    agent_iterations: List[int] = Field(
        ...,
        description="The list of agent iterations to compare",
    )

    def __init__(self, calling_agent: AgentInterface, **data):
        super().__init__(calling_agent=calling_agent, **data)

    async def run(self) -> ToolResult:
        aa = ArchiveAnalyzer("/home/agent/archive")
        try:
            result = aa.compare_iterations_formatted(self.agent_iterations)
            return ToolResult(
                tool_name=self.TOOL_NAME, success=True, output=str(result)
            )
        except Exception as e:
            return ToolResult(tool_name=self.TOOL_NAME, success=False, errors=str(e))

    @classmethod
    def generate_examples(cls) -> list[tuple["CompareIterations", ToolResult]]:
        from ..agents.implementations import DemoAgent

        return [
            (
                cls(calling_agent=DemoAgent(), agent_iterations=[0, 1]),
                ToolResult(
                    tool_name=cls.TOOL_NAME,
                    success=True,
                    output="""
Iteration Comparison:

Iteration 0:
Agent 0 Description:
No description available

Iteration 1:
Agent 1 Description:
No description available

Performance Metrics:
| Metric        |   Iteration 0 | Iteration 1   |
|:--------------|--------------:|:--------------|
| Gsm8K Score   |   0.94        | 0.980         |
| Math Score    |   0           | N/A           |
| Avg Tokens    |   1.06088e+06 | 1216798.000   |
| Avg Cost      |   3.513       | 3.948         |
| Avg Time      | 603.637       | 607.967       |
| Utility Score |   0.47        | 0.980         |
""",
                ),
            ),
        ]
