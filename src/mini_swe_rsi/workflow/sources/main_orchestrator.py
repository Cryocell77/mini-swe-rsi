# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""Main entry point agent."""

import logging

from pathlib import Path

from .coder import CodingAgent
from .problem_solver import ProblemSolvingAgent
from ...config import settings
from ..base_agent import BaseAgent
from ...tools.answer_submission import SubmitAnswer
from ...tools.file_tools import OpenFile, CloseFile
from ...tools.directory_tools import ViewDirectory
from .archive_explorer import ArchiveExplorer
from ...callgraph.manager import CallGraphManager
from ...callgraph.reporting import generate_execution_tree
from ...events import EventBus
from ...types.llm_types import ToolCallContent
from ...types.agent_types import AgentResult, InheritanceConfig
from ...types.event_types import EventType, Event
from ...events.event_bus_utils import get_problem_statement
from ...agents.implementations.reasoner import ReasoningAgent
from ...tools.reasoning_structures.meta_improvement import MetaImprovementReasoningStructure
from ...tools.committee_design import ReviewCommittee

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class MainOrchestratorAgent(BaseAgent):
    """
    Main entry point agent that coordinates other agents.

    This agent:
    1. Analyses the initial problem/request
    2. Coordinates execution of other agents
    3. Synthesizes results into a final answer
    """

    AGENT_NAME = "main"
    AGENT_DESCRIPTION = """
Main entry point for agent execution. Handles initial problem analysis,
planning, and coordination of other agents to arrive at a solution.
"""

    # 3. Your must call the review committee to review your plan before submitting it to an agent, and once you have the revised plan, you must relay it fully to the agent because it cannot see it otherwise. This step is intended to help you create and iteratively refine your implementation plan by drawing it up, and then iteratively passing it by the review committee until there are no significant concerns remaining from the reviewers.

    SYSTEM_PROMPT = """You are an outer-loop entrypoint orchestrator for an agent system which is broken up into sequential agent runss. This is done for context window management purposes; each agent will run for as long as it can / needs and then return a handoff note which will allow you to re-start a new agent with sufficient context about existing work to hit the ground running.

Your single goal is to ensure that the user request is solved or otherwise fulfilled.

Treat the agent calls as self-contained attempts to solve the problem. If the problem is significiant or multi-part, you may break it down into smaller pieces to avoid the risk that an agent will run out of context before completing its assigned sub-task.

Your approach is to:
1. Understand the problem statement to determine its nature and requirements, and whether it needs to be broken up into several agent invocations.

2. Invoke reasoning structures: tools ending in 'reasoning_structure' are here to guide you along certain tasks. If one seems appropriate, invoke it early as soon as you have identified the situation

3. Sequentially delegate to sub-agents: call an appropriate sub-agent, taking care to accurately and completely relay the problem details in order for it to successfully fulfil your intent for this step.

4. Continually evaluate results and progress, by scrutinizing the agent's response. Also understand that the agent's response may contain new and surprising information or results that you don't have and which result from unseen work. Aim to learn from this information, and update your beliefs, while also using critical judgement to assess plausibility and completeness.

5. Verify the work:
   - If results raise doubts or inconsistencies, invoke an additional agent for independent validation.
   - Repeat delegation and review until you're confident in the achieved solution
   - Do not modify results or perform tasks directly; maintain your role as orchestrator.

Your high-level objective is to successfully orchestrate and delegate your agent(s) to deliver a verified solution that you will own, defend and be responsible for through systematic delegation, rigorous evaluation, and comprehensive oversight, without direct intervention in the task execution.
"""

    # Available tools - including answer submission
    AVAILABLE_TOOLS = {
        SubmitAnswer,
        OpenFile,
        CloseFile,
        ViewDirectory,
        MetaImprovementReasoningStructure,
        ReviewCommittee
    }

    # Available agents - high-level specialisations
    # AVAILABLE_AGENTS = set()
    AVAILABLE_AGENTS = {
        ProblemSolvingAgent,
        CodingAgent,
        ArchiveExplorer,
        ReasoningAgent,
    }

    HAS_FILEVIEW = True
    MODEL = settings.MODEL
    TEMPERATURE = 0.666
    MAX_ITERATIONS = 500
    INHERITANCE = InheritanceConfig()

    def __init__(self, parent: BaseAgent | None = None, workdir: Path | None = None, logdir: Path | None = None, debug_mode: bool = False, **data):
        super().__init__(parent=parent, workdir=workdir, logdir=logdir, debug_mode=debug_mode, **data)

    async def construct_core_prompt(self) -> str:
        """Construct the core prompt for the main agent."""

        problem_statement = await get_problem_statement()
        if not problem_statement or problem_statement == "":
            raise ValueError(
                "The initial request cannot be None in the main agent's context"
            )

        # 3. Create a plan of how to proceed or what feature to implement, refine this using the review_committee until no significant concerns remain, and then make sure you relay this plan entirely to the agent you call
        prompt = f"""Your task is to orchestrate one or more agents sequentially to solve the problem. Here is the problem to solve:

<problem_statement>
{problem_statement}
</problem_statement>

As the entrypoint orchestrator, your role is solely to coordinate and direct agents, communicating results between them.

Agents won't see or be able to directly reference the initial problem statement that you can see. It is your job to effectively decompose it, and provide clear, complete and comprehensive direction to your agent(s).

You have access to file and directory viewing tools to help you understand the context and best fulfil your role as the router and entrypoint orchestrator.

You have access to reasoning structures (tool with names ending in _reasoning_structure). If you find an appropriate one, you *must* invoke this reasoning structure tool.

Your high level approach should be to
1. Understand the problem, decompose it, and plan out how to break it down into agent invocations.
2. Invoke any appropriate reasoning structures - skipping this step if none are appropriate
3. Invoke the agent, carefully articulating and relaying the problem. Dynamically re-plan and integrate the results from each agent result.
4. For complex problems, verify the solution
5. Synthesise a final answer by consolidating the work of the agent(s) into a coherent final answer that fully addresses the problem statement. Note that for some problems which don't explicitly resemble questions, you may simply submit 'done' as the answer once undertaking the work elsewhere.

Important guidelines:
- You MUST make at least one agent call, otherwise you have failed as the router
- You MUST make use of reasoning structures, however only if one is immediately applicable to the problem at hand
- Trust your sub-agents' expertise but exercise critical judgement over their answers
- DO NOT attempt to do the substantive work yourself
- You must have made at least one call to the submit_answer tool before completing. If the problem does not have an obvious answer to submit, simply submit 'done' when the task has been completed elsewhere.
"""

        return prompt

    @classmethod
    def generate_examples(cls) -> list[tuple[BaseAgent, AgentResult]]:
        return []

    async def _handle_agent_call(self, agent_content: ToolCallContent):
        await super()._handle_agent_call(agent_content)

        # Generate the execution tree and add a notification. This is so that
        # the main agent can see what work the subagent did, and avoid
        # duplicate work.
        try:
            cg = await CallGraphManager.get_instance()
            exec_tree = await generate_execution_tree(cg, include_all_events=True)

            event_bus = await EventBus.get_instance()
            await event_bus.publish(
                Event(
                    type=EventType.OVERSEER_NOTIFICATION,
                    content=f"The state of this agent run is now:\n{exec_tree}",
                    metadata={"forced": "true"},
                ),
                self._id,
            )
        except Exception as e:
            logger.info(f"Could not construct callgraph for main: {e}")
            pass


# Example usage:
if __name__ == "__main__":
    import asyncio

    async def main():
        workdir = Path("/tmp/workdir")
        logdir = workdir / "agent_outputs"
        problem = "Find out our public IP address."
        main = MainOrchestratorAgent(workdir=workdir, logdir=logdir, debug_mode=False)
        event_bus = await EventBus.get_instance()
        await event_bus.publish(Event(type=EventType.PROBLEM_STATEMENT, content=problem), main._id)
        result = await main.execute()
        print(result)
        # Now check logdir / answer.txt
        # TODO: add assert based tests here

    asyncio.run(main())
