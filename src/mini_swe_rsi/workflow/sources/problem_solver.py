# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
from typing import List
from pathlib import Path
from pydantic import Field


from .reasoner import ReasoningAgent
from ..base_agent import BaseAgent
from ...config import settings
from ...tools.calculator import Calculator
from ...tools.directory_tools import ViewDirectory
from ...tools.execute_command import ExecuteCommand
from ...tools.file_tools import OpenFile, CloseFile
from ...tools.edit_tools import OverwriteFile
from ...tools.ripgrep_tool import RipGrepTool
from ...tools.committee_design import ReviewCommittee
from .coder import CodingAgent
from ...utils.metrics import make_random_agent_metrics
from ...types.agent_types import AgentStatus, AgentResult
from ...events.event_bus_utils import get_problem_statement


class ProblemSolvingAgent(BaseAgent):
    """
    A multi-purpose problem-solving agent with access to all tools and capabilities.

    This agent can:
    1. Analyze and decompose complex problems
    2. Plan and execute solutions systematically
    3. Use a wide range of tools and agents
    4. Validate and refine solutions
    5. Handle errors and edge cases
    6. Document and explain its process
    """

    AGENT_NAME = "general_problem_solver"

    AGENT_DESCRIPTION = """
Your default agent for all tasks. Highly versatile, with broad tool access. Best for tasks requiring multiple capabilities or when specific agent choice isn't obvious.

Note that the agent will not have the context that you have / be able to see the initial problem statement verbatim. It is up to you to accurately relay this to the sub-agent, or decompose it into sub-tasks if it is very long and repeating it verbatim would be slow and costly.

Example capabilities
- Problem decomposition and analysis
- General purpose writing tasks
- Basic Coding (although not specialised)
- Quick System and file operations
- Mathematical computation
- Running shell commands

Choose when:
- Specific agent isn't clearly better
- Need flexible approach

Avoid when:
- Task fits squarely in another agent's specialty
- Requires deep domain expertise"""

    SYSTEM_PROMPT = """You are a very-competent problem solver who finds solutions swiftly and effectively.

You should
1. Understand the sense of the problem you have been provided
2. Identify the optimal tools and methods you can use to solve your task
3. Swiftly execute on the problem
4. Continuously validate and check your work

Aim for simple, elegant and correct solutions.
"""

    # Available tools - complete access to all tools
    # NOTE: ExitAgent and ReturnResult are automatically included
    AVAILABLE_TOOLS = {
        Calculator,
        ViewDirectory,
        ExecuteCommand,
        OpenFile,
        CloseFile,
        OverwriteFile,
        RipGrepTool,
        ReviewCommittee,
    }

    # Available agents
    # AVAILABLE_AGENTS = set()
    AVAILABLE_AGENTS = {ReasoningAgent, CodingAgent}

    HAS_FILEVIEW = True

    MODEL = settings.MODEL
    TEMPERATURE = 0.666

    # Agent parameters
    problem_statement: str = Field(
        ...,
        description="The problem or request you want the problem solver agent to solve",
    )
    previous_agent_runs: List[str] = Field(
        default=[],
        description="A list of descriptions of previous work undertaken by other agents, the context from which this agent would benefit from knowing. This helps to avoid duplicate work.",
    )
    requirements: List[str] = Field(
        default=[],
        description="A list of very specific and low-level criteria which must be met or become valid for the sub-agent to consider its work done.",
    )

    def __init__(
        self,
        parent: BaseAgent | None = None,
        workdir: Path | None = None,
        logdir: Path | None = None,
        debug_mode: bool = False,
        **data,
    ):
        super().__init__(
            parent=parent, workdir=workdir, logdir=logdir, debug_mode=debug_mode, **data
        )

    async def construct_core_prompt(self) -> str:
        """Construct the core prompt for problem solving."""

        # initial_request = await get_problem_statement()
        # if initial_request is None or initial_request == "":
        #     raise ValueError(
        #         "The initial request was not provided to the problem solver"
        #     )

        prompt = f"""Here is the problem you have been asked to solve:

<problme_to_solve>
{self.problem_statement}
</problem_to_solve>
"""

        if self.previous_agent_runs:
            prompt += "\n\nWork Previously Completed:"
            prompt += "\nYou should pay attention to this list to avoid duplicating work. Also note that this list is for work completed by other agents, which aren't 100% reliable, so treat claims with appropriate caution, and verify accordingly."
            for work in self.previous_agent_runs:
                prompt += f"\n- {work}"

        if self.requirements:
            prompt += "\n\nSpecific requirements which must be met before you can consider the work 'done':"
            for req in self.requirements:
                prompt += f"\n- {req}"

        prompt += "\n\nReturn your answer when complete."

        return prompt

    @classmethod
    def generate_examples(cls) -> list[tuple["BaseAgent", AgentResult]]:
        """Generate example uses of the tool with their expected outputs."""
        examples = [
            # Example 1: Mathematical Problem Solving
            (
                cls(
                    problem_statement="""Solve the following system of equations:
3x + 2y = 12
x - y = 1""",
                    requirements=[
                        "Show the full answer derivation",
                        "Verify the solution numerically using Python",
                    ],
                ),
                AgentResult(
                    agent_name=cls.AGENT_NAME,
                    status=AgentStatus.SUCCESS,
                    result="""Solution found: x = 4, y = 3

Process:
1. Used elimination method
2. Verified by substitution in a Python script
3. Checked both equations
4. All validation criteria met""",
                    metrics=make_random_agent_metrics(
                        tools_enabled=True, agents_enabled=True
                    ),
                ),
            ),
            # Example 2: Code Analysis and Modification
            #                   (
            #                 cls(
            #                     problem_statement="""Fix the performance issue in process_data() agent:
            #
            # - Current implementation uses O(n²) time
            # - Need to optimize to O(n) complexity
            # - Maintain existing API contract""",
            #                     requirements=[
            #                         "Keep existing agent signature",
            #                         "Maintain thread safety",
            #                         "Add performance tests",
            #                     ],
            #                 ),
            #                 AgentResult(
            #                     agent_name=cls.AGENT_NAME,
            #                     status=AgentStatus.SUCCESS,
            #                     result="""Optimized process_data() agent:
            #
            # 1. Analyzed existing implementation
            # 2. Identified quadratic loop pattern
            # 3. Refactored to use hash table
            # 4. Added performance tests
            # 5. Verified thread safety
            # 6. Maintained API compatibility
            #
            # Performance improved:
            # - Before: O(n²) time, O(1) space
            # - After: O(n) time, O(n) space
            # - Verified with test suite""",
            #                     metrics=make_random_agent_metrics(
            #                         tools_enabled=True, agents_enabled=True
            #                     ),
            #                 ),
            #             ),
        ]
        return examples
