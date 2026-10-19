# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""Coding-specialised agent"""

from typing import List
from pathlib import Path
from pydantic import Field

from .reasoner import ReasoningAgent
from ..base_agent import BaseAgent
from ...config import settings
from ...tools.calculator import Calculator
from ...tools.ripgrep_tool import RipGrepTool
from ...tools.directory_tools import ViewDirectory
from ...tools.execute_command import ExecuteCommand
from ...tools.file_tools import OpenFile, CloseFile
from ...tools.edit_tools import OverwriteFile
from ...utils.metrics import make_random_agent_metrics
from ...events.event_bus_utils import get_problem_statement
from ...tools.reasoning_structures.coding import CodingReasoningStructure
from ...types.agent_types import AgentStatus, AgentResult, InheritanceConfig
from ...tools.committee_design import ReviewCommittee


class CodingAgent(BaseAgent):
    """
    An agent specialised for coding tasks.
    """

    AGENT_NAME = "software_developer"

    AGENT_DESCRIPTION = """A specialised agent for coding tasks. You must delegate to this agent when the problem at hand involves solving any sort of coding problem.

Note that this agent doesn't get to see your current context, or the initial request or problem statement. It is up to you to accurately relay this to the sub-agent or decompose it into sub-tasks if it is very long and repeating it verbatim would be slow and costly, as well as any relevant information in your dialogue history.

When specifying your request, you should not make reference to part of the original request, problem statement or your dialogue history, since the coding agent cannot see it. Accurately relay such parts of the problem directly to the subagent.

Core competencies:
- Extracting requirements from the problem statement
- Prototyping solutions
- Microbenchmarking
- Code writing and modification
- Testing and debugging
- System and file operations

Choose when:
- The task clearly has a programming or coding component to it
- You wish to write a program

Avoid when:
- The task is very quick
- Task fits squarely in another agent's specialty
"""

    # 2. Invoke any reasoning structures made available to you, if appropraite
    SYSTEM_PROMPT = """You are an expert software engineer with deep knowledge across multiple programming languages, architectures, and paradigms. Your role is to write, analyze, and modify code with precision and efficiency.

Key principles:
1. Write clean, maintainable code that solves the core problem
2. Consider performance implications and optimization opportunities
3. Handle edge cases and error conditions gracefully
4. Follow language-specific best practices and idioms
5. Include appropriate documentation and comments
6. Add tests where beneficial - avoid using testing frameworks or mocks where possible

Your methodology:
1. Analyze requirements and constraints thoroughly
2. Invoke the review committee to refine your plan before you proceed with it
3. Thoroughly search the codebase for any documentation and view it
4. Design solutions that balance simplicity and robustness
5. Implement with attention to detail
6. Test and validate against requirements
7. Refactor and optimize when necessary
8. Document decisions and important considerations

Remember:
- Performance should be considered but not prematurely optimized
- Code should be self-documenting where possible
- Tests should focus on behavior, not implementation
- Error handling should be comprehensive but not excessive"""

    # Available tools
    AVAILABLE_TOOLS = {
        Calculator,
        ViewDirectory,
        ExecuteCommand,
        RipGrepTool,
        OpenFile,
        CloseFile,
        OverwriteFile,
        # CodingReasoningStructure,
        ReviewCommittee,
    }

    # Available agents
    AVAILABLE_AGENTS = {ReasoningAgent}

    HAS_FILEVIEW = True
    MODEL = settings.MODEL
    TEMPERATURE = 0.666
    MAX_ITERATIONS = 1000
    INHERITANCE = InheritanceConfig()

    # Agent parameters
    programming_instructions: str = Field(
        ...,
        description="Clear and comprehensive instructions about what software development task to carry out.",
    )
    previous_agent_runs: List[str] = Field(
        default=[],
        description="A string list of descriptions of previous work undertaken by other agents, the context from which this agent would benefit from knowing. This helps to avoid duplicate work.",
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
        initial_request = await get_problem_statement()
        if initial_request is None:
            raise ValueError("The initial request was not provided to the coding agent")

        reasoning_structure_prompt = f"""Here are your specific instructions that you must follow:

<your_instructions>
{self.programming_instructions}
</your_instructions>

As a professional and experienced programmer, your approach is to

- Write clean, maintainable code that solves the core problem
- Consider performance implications and optimization opportunities
- Handle edge cases and error conditions gracefully
- Follow language-specific best practices and idioms
- Include appropriate documentation and comments
- Add tests where beneficial - avoid using testing frameworks or mocks where possible

If the request is clearly exploratory in nature, or a trivial code edit, then
you may directly implement the solution. If the problem is not trivial and
requires a careful and rigorous approach, then you must invoke one of your
reasoning structures (tools ending in _reasoning_structures), which will guide
you through the coding problem.

NOTE:
  - don't create virtual environments
  - avoid pytest and mocks if you can; prefere end-to-end scripts
  - if the request is clearly exploratory in nature, then you may bypass the rigorous procedure above, and address it appropriately
  - call your reasoning agent if you are stuck on a tricky algorithmic or mathematical problem, to help you gain insight and make progress
  - remember to invoke your reasoning structures, if appropriate
"""
        prompt = f"""Here are your specific instructions that you must follow:

<your_instructions>
{self.programming_instructions}
</your_instructions>

As a professional and experienced programmer, your approach is to

- Write clean, maintainable code that solves the core problem
- Consider performance implications and optimization opportunities
- Handle edge cases and error conditions gracefully
- Follow language-specific best practices and idioms
- Include appropriate documentation and comments
- Add tests where beneficial - avoid using testing frameworks or mocks where possible

NOTE:
  - don't create virtual environments
  - avoid pytest and mocks if you can; prefere end-to-end scripts
  - if the request is clearly exploratory in nature, then you may bypass the rigorous procedure above, and address it appropriately
  - call your reasoning agent if you are stuck on a tricky algorithmic or mathematical problem, to help you gain insight and make progress
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

        prompt += "\n\nMake absolutely sure that you have covered all the requirements before you exit. Return your answer when complete."

        return prompt

    @classmethod
    def generate_examples(cls) -> list[tuple["BaseAgent", AgentResult]]:
        """Generate example uses of the tool with their expected outputs."""
        examples = [
            # Example 1: Generic Programming Task - Building a Thread-safe Cache
            (
                cls(
                    programming_instructions="""Create a thread-safe LRU cache implementation for our web application.""",
                    previous_agent_runs=[
                        "CodebaseExplorer found existing cache implementations in other parts of the system",
                        "FileEditor prepared test directory structure",
                    ],
                    requirements=[
                        "Supports concurrent access",
                        "O(1) lookup and insertion time",
                        "Evicts least recently used items when capacity is reached",
                        "Supports optional TTL for entries",
                    ],
                ),
                AgentResult(
                    agent_name=cls.AGENT_NAME,
                    status=AgentStatus.SUCCESS,
                    result="""Successfully implemented thread-safe LRU cache:

1. Created cache implementation in cache/lru_cache.py
2. Implemented core functionality:
   - Thread-safe operations using asyncio.Lock
   - O(1) operations with OrderedDict
   - TTL support with background cleanup
3. Added comprehensive tests in tests/test_lru_cache.py
4. Benchmarks show:
   - Get/set operations <1ms
   - Memory stable at high concurrency
   - No deadlocks detected

All requirements met and validated through test suite.""",
                    metrics=make_random_agent_metrics(
                        tools_enabled=True, agents_enabled=True
                    ),
                ),
            ),
            # Example 2: Agent-Specific Task - Adding Code Quality Tools
            (
                cls(
                    programming_instructions="""Add a file auto-formatting tool to the agent's tool system, and make it available to the coding agent.

This tool should:
- Use black for code formatting
- Use isort for import sorting
- Strip trailing whitespace
""",
                    requirements=[
                        "Tool extends the BaseTool base class",
                        "Must be added to CodingAgent's AVAILABLE_TOOLS",
                        "Should preserve file permissions",
                        "Must handle large files efficiently",
                        "Formatting matches black and isort standards",
                        "No whitespace remains at line ends",
                        "The system prompt during the test showed the tool and examples correctly",
                        "The tool use appears successfully in the execution trace of test agent runs",
                    ],
                    previous_agent_runs=[
                        "Identified from past benchmark runs that code quality tests were suffering from poor formatting",
                    ],
                ),
                AgentResult(
                    agent_name=cls.AGENT_NAME,
                    status=AgentStatus.SUCCESS,
                    result="""Successfully implemented FormatFile tool:

1. Created a new FormatFile tool in tools/file_tools.py
2. Added tool registration to CodingAgent's AVAILABLE_TOOLS
3. Ran end-to-end test using agent system - formatting operation completed successfully
4. Verified in agent logs that:
   - Tool properly registered and available to coding agent
   - Format operations completed without errors
   - File permissions preserved
   - All files formatted to specification

Tool is now ready for production use.""",
                    metrics=make_random_agent_metrics(
                        tools_enabled=True, agents_enabled=True
                    ),
                ),
            ),
            (
                cls(
                    programming_instructions="""Analysis of the archive shows that the GeneralPurposeAgent frequently submits solutions without proper validation, leading to avoidable errors.
Update the core prompt in the GeneralPurposeAgent to add an explicit validation step after solution generation.

The agent should be forced to:
1. State its validation criteria explicitly
2. Test its solution against these criteria
3. Make any necessary refinements
4. Document what was validated
""",
                    requirements=[
                        "GeneralPurposeAgent prompts have been updated to induce the desired behaviour",
                        "Examination of the execution trace on the test problem shows the validation step",
                        "The execution trace show that the original behaviours are intact",
                    ],
                    previous_agent_runs=[
                        "Analysis of the benchmark archive shows 23% of failures due to insufficient solution validation",
                    ],
                ),
                AgentResult(
                    agent_name=cls.AGENT_NAME,
                    status=AgentStatus.SUCCESS,
                    result="""Successfully updated GeneralPurposeAgent's core prompt:

1. Modified the core prompt to add an additional verification step
2. Updated prompt to require:
   - Explicit validation criteria definition
   - Systematic testing against criteria
   - Documentation of validation results
   - Refinement based on validation findings

3. Ran end-to-end test which demonstrated:
   - Agent explicitly stated validation criteria for sum operation
   - Tested empty list case and negative numbers
   - Documented validation steps and results
   - Made refinements based on validation findings
""",
                    metrics=make_random_agent_metrics(
                        tools_enabled=True, agents_enabled=True
                    ),
                ),
            ),
        ]
        return examples
