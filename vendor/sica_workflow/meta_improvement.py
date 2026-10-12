# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import logging

from pydantic import PrivateAttr

from .sequential import Step, ToolBasedReasoningStructure, _make_id
from ...types.tool_types import ToolResult
from ...types.agent_types import AgentInterface

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class MetaImprovementReasoningStructure(ToolBasedReasoningStructure):

    TOOL_NAME = "meta_improvement_reasoning_structure"
    TOOL_DESCRIPTION = """If you have been instructed to meta-improve the coding agent framework, then call this reasoning structure as the first thing you do.

This tool will guide you step by step through successfully carrying out the agent meta-improvement. You should carefully follow the step instructions issued by this tool and the subsequent step results.

DO NOT call this tool in any other scenario: if the file editing task does not involve meta-improvement, avoid this tool.
"""
    _steps: list[Step] = PrivateAttr(default_factory=lambda: [
        Step(
            identifier=_make_id(),
            instruction="Use the archive explorer agent and agent_change_log.md file to specifically identify the features in the agent code, particularly those that we introduced in past iterations, that ARE NOT working and may need to be removed. (We will focus on positive features that are working in a later step, for now, identify the negatives.)",
            done_description="We have carefully inspected the agent code and determined what (if any) features are demonstrably not working.",
            failed_description="An error arose or the information could not be obtained for some reason",
        ),
        Step(
            identifier=_make_id(),
            instruction="Use the archive explorer agent and agent_change_log.md file to get a report of features in the agent code that are specifically working well and should be kept or doubled-down upon, as well as features that maybe aren't working right now, but do show promise and with more work could reasonably become a valuable part of the system.",
            done_description="We have carefully inspected the agent code and determined what (if any) features are demonstrably working well.",
            failed_description="An error arose or the information could not be obtained for some reason",
        ),
        Step(
            identifier=_make_id(),
            instruction="Come up with a concrete improvement to the coding agent system to work on at this iteration. Focus on things like improving the mechanics of code editing, integrating developer tools, strategies to make the agent faster, strategies to increase the agent's generated code quality, code correctness verification strategies, dead code or poorly performing features to be deleted, or any other creative ways to increase the benchmark performance. You may choose to use the archive explorer again or you may alway wish to set off a sub agent with web tools to research new ideas on the internet (such as reading arxiv papers or blogs, etc), being careful to list out the implementation ideas already covered in the topics to avoid. You MUST call the `meta_improvement_review_committee` with the proposed feature and iterate with it until there are no significant reviewer concerns.",
            done_description="We have obtained a tasteful, non-trivial, realistic, interesting and reasonable idea for a new feature to implement in the coding agent, which has been passed by the review committee and addressed any significant concerns from the reviewers",
            failed_description="An error arose or the information could not be obtained for some reason",
        ),
        # Step(
        #     identifier=_make_id(),
        #     instruction="Commit to the settled upon feature by asking a sub-agent to create a new log in the agent_change_log.md file with a concise feature description.",
        #     done_description="After careful reasoning and trading-off, we have come up with and clearly articulated a single idea of what to implement in this meta-improvement round, had a sub-agent write it in the agent_change_log.md for this iteration, but have not yet made a start on the implementation.",
        #     failed_description="The agent has jumped the gun and started implementing the feature before this step is complete",
        # ),
        # Step(
        #     identifier=_make_id(),
        #     instruction="Select the base agent version that we want to use, based on the benchmark results, and make sure that the code in /home/agent/workdir contains this agent version.",
        #     done_description="After careful reasoning and trading-off, we have come up with and clearly articulated a single idea of what to implement in this meta-improvement round",
        # ),
        Step(
            identifier=_make_id(),
            instruction="Use a software engineering agent to completely and thoroughly implement the feature we have decided to implement at this iteration.",
            done_description="The software engineer has completed without errors after having implemented the feature, or as many subsequent attempts have been made to set off sub-agents and see the feature to completion.",
        ),
        Step(
            identifier=_make_id(),
            instruction="Ensure that the testing strategy for the feature implementation has been carried out: either running unit tests for framework or tool features, or simple end-to-end tests of new sub-agents or prompt modifications. For unit tests, use the existing `pytest` based setup. For end-to-end tests, run something like `python -m workdir.agent -p '<some SIMPLE agent prompt here...>' --workdir /tmp/workdir --logdir /tmp/test_agent_logs --timeout 300`. Make sure to use temporary directories, and after you have inspected the outputs of the test agent run, make sure to delete the test agent workdir and logdir. Check that the tool or agent is being used (you might have forgotten to import it or add it to AVAILABLE_TOOLS / AVAILABLE_AGENTS). You should then thoroughly inspect the outputs in /tmp/workdir as well as the traces in /tmp/test_agent_logs. Are the outputs as expected? Are all the tool and function definitions showing up correctly with instructive examples? Is the full execution trace reasonable?",
            done_description="Satisfy yourself that the tests are correct and reasonable, and that the tests pass correctly when run. You are satisfied that the agent is stable, reliable and ready to progress to the next iteration",
        ),
        Step(
            identifier=_make_id(),
            instruction="Ensure that the description.txt file at /home/agent/workdir/description.txt has a concise description of what has been changed, refined or otherwise implemented in this agent iteration. Call an appropriate sub-agent (e.g. general_problem_solver) with editing tools to do this.",
            done_description="The description.txt file has been updated",
        ),
        Step(
            identifier=_make_id(),
            instruction="Ensure that the agent_change_log.md file at /home/agent/workdir/agent_change_log.md reports on the effectivenss of earlier interventions as evidenced by the benchmark data (i.e. change any 'pending' features' status to report on their performance), and contains a log entry for this agent improvement iteration with the effectiveness of this iterations's features as 'pending'. Add new sections to the bottom of the file not the top. Remember you must call a sub-agent with editing tools (such as general_problem_solver) if the file need to be edited.",
            done_description="The description.txt and agent_change_log.md file has been updated to reflect all new information.",
        ),
    ])

    def __init__(self, calling_agent: AgentInterface, **data):
        super().__init__(calling_agent=calling_agent, **data)

    @classmethod
    def generate_examples(cls) -> list[tuple["MetaImprovementReasoningStructure", ToolResult]]:
        from ...agents.implementations import DemoAgent

        return [
            (
                cls(calling_agent=DemoAgent()),
                ToolResult(
                    tool_name=cls.TOOL_NAME,
                    success=True,
                    output="The first step in the meta improvement process is: ...",
                ),
            ),
        ]

# class MetaImprovementStructureSubagents(BaseTool):
#
#     TOOL_NAME = "meta_improvement_structure"
#     TOOL_DESCRIPTION = """A specialised structure for the meta-improvement task"""
#
#     def __init__(self, calling_agent: Any, **data):
#         from ...agents.base_agent import BaseAgent
#         assert isinstance(calling_agent, BaseAgent)
#         super().__init__(calling_agent=calling_agent, **data)
#
#     async def run(self) -> ToolResult:
#
#         # 1. Explore the archive
#         validated_agent = ArchiveExplorer(analysis_focus="nothing in particular")
#         #     agent=validated_agent,
#         #     parent=self._calling_agent,
#         #     inherit=validated_agent.INHERITANCE,  # note, we can validate this
#         #     available_tools=validated_agent.AVAILABLE_TOOLS,
#         #     available_agents=validated_agent.AVAILABLE_AGENTS,
#         # )
#         await execute_agent_call(validated_agent, self._calling_agent)
#
#         # 2. Explore a) what's not working and needs to be removed, b) what is working well and should be kept  / doubled down on, c) what isn't working now but shows promise and should be fixed
#
#         # 3. If we decide we want to add something new, do the research here
#         #  a) isolated ideation on the codebase
#         #  b) archive-grounded ideation
#         #  c) web-search inspired solutions (use 'deep research')
#
#         # 4. Pick the agent code version we want to use as base
#
#         # 5. Set off a software engineer to implement the thing
#
#         # 6. Set off a software engineer to validate the thing with unit tests
#
#         # 7. Set off a software engineer to validate the thing with integration tests
#
#         # 8. update documentation and wrap up
#
#         # Optional: are the benchmarks saturating?
#         #
#         # If so, let's remove saturated ones, and curate a new one.
#
#         return ToolResult(
#             tool_name=self.TOOL_NAME,
#             success=True,
#             output="",
#             # errors=f"Error in sequential reasoning: {e}"
#         )
#
#     @classmethod
#     def generate_examples(cls) -> list[tuple["MetaImprovementStructure", ToolResult]]:
#         from ...agents.implementations import DemoAgent
#
#         return [
#             (
#                 cls(calling_agent=DemoAgent()),
#                 ToolResult(
#                     tool_name=cls.TOOL_NAME,
#                     success=True,
#                     output={
#                         "message": "Successfully performed the meta-improvement step",
#                     },
#                 ),
#             ),
#         ]
