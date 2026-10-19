# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""
A testbed sequential reasoning structure, which manipulates the content of the
current agent's messages in order to achieve the sequential computation.
"""
import logging

from uuid import uuid4
from typing import Any
from pydantic import BaseModel, Field, PrivateAttr
from dataclasses import dataclass

from ..base_tool import BaseTool, tool_registry
from ...types.tool_types import ToolResult
from ...types.agent_types import AgentInterface
from ...types.llm_types import FCI

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# NOTE: currently unused, but could be used, for instance, if generating a
# sequential reasoning structure at runtime.
# class StepDescription(BaseModel):
#     thinking: str = Field(..., description="Think about what this next step should be and why")
#     intent: str = Field(..., description="A very short line giving the intent / title of the step")
#     description: str = Field(..., description="A complete description of what should be achieved in this step")
#     done_description: str = Field(..., description="A clear description of what it means for this step to be 'done'")
#     failed_description: str = Field(..., description="A clear description of what it means for this step ot have failed")
#     out_of_scope_description: str = Field(..., description="Short description of the next steps which are out of scope or beyond the remit of this step")


@dataclass
class Step:
    identifier: str
    # A complete description of what should be achieved in this step
    instruction: str
    # A clear description of what it means for this step to be 'done'
    done_description: str | None = None
    # A clear description of what it means for this step ot have failed
    failed_description: str | None = None
    # Short description of the next steps which are out of scope or beyond the remit of this step
    out_of_scope_description: str | None = None


@dataclass
class InvocationState:
    invocation_id: str
    steps: list[Step] | Any   # TODO: consider supporting branching and more complex control flow
    current_step_id: str
    current_step_complete_tool: type[BaseTool] | None = None


def create_step_tool(invocation_id: str, step: Step) -> type[BaseTool]:
    """
    A closure to create a unique tool for every step of the sequence.
    """
    description = f"""You MUST call this tool as soon as you have completed step {step.identifier}."""
    if step.done_description:
        description += f"\nThe step {step.identifier} is done when: {step.done_description}"
    if step.failed_description:
        description += f"\nThe step {step.identifier} has failed if: {step.failed_description}. If you detect this failure mode, then invoke this tool immediately."

    class SequentialReasoningStep(BaseTool):
        TOOL_NAME = f"{step.identifier}_complete"
        TOOL_DESCRIPTION = description
        EPHEMERAL = True

        success: bool = Field(default=True, description=f"Whether the step succeeded. A failure is if: {step.failed_description}" if step.failed_description else "Whether the step succeeded.")

        # Boilerplate tool init
        def __init__(self, calling_agent: AgentInterface, **data):
            super().__init__(calling_agent=calling_agent, **data)

        async def run(self) -> ToolResult:
            parent_agent: AgentInterface = self._calling_agent

            try:
                invocation = parent_agent._local_state.get(invocation_id)
                if not invocation:
                    raise ValueError("Could not find reasoning structure in local state.")

                # 1. Decide what the next step should be based on the success
                # and potentially other factors; should we exit early, branch,
                # etc?
                # For now, we just get the next state
                idx = next((i for i, v in enumerate(invocation.steps) if v.identifier == step.identifier), -1)
                final_step = idx + 1 == len(invocation.steps)
                next_step = invocation.steps[idx+1] if not final_step else None

                # 1. Remove the current tool from the registry
                if invocation.current_step_complete_tool:
                    current_tool = invocation.current_step_complete_tool
                    del tool_registry[current_tool.TOOL_NAME]
                    self._calling_agent._available_tools.remove(current_tool)

                if final_step or not self.success:
                    del parent_agent._local_state[invocation_id]

                    if not self.success:
                        output=f"Reasoning structure exited due to error in step {step.identifier}. Please proceed to complete the rest of the task as appropriate."
                    else:
                        output=f"Reasoning structure complete. Please proceed to complete the rest of the task as appropriate."

                    return ToolResult(
                        tool_name=self.TOOL_NAME,
                        success=True,
                        output=output,
                    )
                else:
                    assert next_step

                    # 2. Register the next completion tool
                    next_completion_tool = create_step_tool(invocation_id, next_step)
                    tool_registry[next_completion_tool.TOOL_NAME] = next_completion_tool
                    parent_agent._available_tools.add(next_completion_tool)
                    invocation.current_step_complete_tool = next_completion_tool

                    # 3. Issue instructions for next tool call
                    step_content = f"Step {step.identifier} marked as complete. Let's move on to the next step, with step id {next_step.identifier}\n"
                    step_content += f"\nStep {step.identifier} Instructions {'='*31}\n\n{next_step.instruction}\n\nEnd Step Instructions {'='*36}\n\n"
                    step_content += f"Very Important: once you have completed step {next_step.identifier}, you must call the following completion tool.\n"
                    if parent_agent.MODEL.fci == FCI.UNCONSTRAINED:
                        step_content += next_completion_tool.to_prompt_format(parent_agent.MODEL.arg_format)
                    else:
                        step_content += next_completion_tool.to_plain_prompt_format(parent_agent.MODEL.arg_format)

                    return ToolResult(
                        tool_name=self.TOOL_NAME,
                        success=True,
                        output=step_content
                    )

            except Exception as e:
                return ToolResult(
                    tool_name=self.TOOL_NAME,
                    success=False,
                    errors=f"Error in completing step: {e}.\nPlease proceed with the task as you see fit."
                )

        @classmethod
        def generate_examples(cls) -> list[tuple["SequentialReasoningStep", ToolResult]]:
            from ...agents.implementations import DemoAgent

            return [
                (
                    cls(
                        calling_agent=DemoAgent(),
                        success=True,
                    ),
                    ToolResult(tool_name=cls.TOOL_NAME, success=True, output=f"Step <id> complete. The next step is: ...")
                ),
            ]

    return SequentialReasoningStep


def _make_id(prefix: str = "step") -> str:
    return f"{prefix}_{uuid4().hex[-4:]}"


class ToolBasedReasoningStructure(BaseTool):
    """
    Initialises the sequential reasoning structure.
    """

    TOOL_NAME = "example_reasoning_structure"
    TOOL_DESCRIPTION = """Call this tool when asked to 'do the ABC'

This tool will guide you step by step through doing the abc. After invoking
this tool, you must complete each of the steps it gives you in order.
"""

    # Example of hard-coded steps
    _steps: list[Step] = PrivateAttr(default_factory=lambda: [
        Step(
            identifier=_make_id(),
            instruction="Print 'a' in a file called 'a.txt'",
            done_description="The file 'a.txt' has been created",
            failed_description="An error arose or the file could not be created for some reason",
        ),
        Step(
            identifier=_make_id(),
            instruction="Print 'b' in a file called 'b.txt'",
            done_description="The file 'b.txt' has been created",
            failed_description="An error arose or the file could not be created for some reason",
        ),
        Step(
            identifier=_make_id(),
            instruction="Print 'c' in a file called 'c.txt'",
            done_description="The file 'c.txt' has been created",
            failed_description="An error arose or the file could not be created for some reason",
        ),
    ])

    # steps: list[Step] = Field(..., description="The sequence of steps that must be carried out.")

    def __init__(self, calling_agent: AgentInterface, **data):
        super().__init__(calling_agent=calling_agent, **data)

    async def run(self) -> ToolResult:
        try:
            parent_agent: AgentInterface = self._calling_agent
            invocation_id = _make_id("reasoning_structure_invocation")
            first_step = self._steps[0]

            # Steup the invocation state
            invocation = InvocationState(
                invocation_id=invocation_id,
                steps=self._steps,
                current_step_id=first_step.identifier,
                current_step_complete_tool=None,
            )

            # Setup the first step completion tool
            first_step_complete_tool = create_step_tool(invocation.invocation_id, first_step)
            invocation.current_step_complete_tool = first_step_complete_tool

            # Add the tool to the registry (to allow it to be parsed) and current
            # agent (to make it valid to call)
            tool_registry[first_step_complete_tool.TOOL_NAME] = first_step_complete_tool
            parent_agent._available_tools.add(first_step_complete_tool)

            # Register this invocation state with the agent's local state
            parent_agent._local_state[invocation_id] = invocation

            # TODO: explore issuing user messages or assistant prefill messages
            # event_bus = await EventBus.get_instance()
            # await event_bus.publish(
            #     Event(
            #         type=EventType.EXTERNAL_MESSAGE,
            #         content=f"Now let's start the next step ({first_step_id}): {first_step_instr}."
            #     ),
            #     parent_agent._id,
            # )

            step_content = f"Let's start the first step, with step id {first_step.identifier}\n"
            step_content += f"\nStep {first_step.identifier} Instructions {'='*31}\n\n{first_step.instruction}\n\nEnd Step Instructions {'='*36}\n\n"
            step_content += f"Very Important: once you have completed this step, you must call the following tool.\n"
            if parent_agent.MODEL.fci == FCI.UNCONSTRAINED:
                step_content += first_step_complete_tool.to_prompt_format(parent_agent.MODEL.arg_format)
            else:
                step_content += first_step_complete_tool.to_plain_prompt_format(parent_agent.MODEL.arg_format)

            return ToolResult(
                tool_name=self.TOOL_NAME,
                success=True,
                output=step_content,
            )
        except Exception as e:
            return ToolResult(
                tool_name=self.TOOL_NAME,
                success=False,
                errors=f"Error in sequential reasoning: {e}"
            )

    @classmethod
    def generate_examples(cls) -> list[tuple["ToolBasedReasoningStructure", ToolResult]]:
        from ...agents.implementations import DemoAgent

        return [
            (
                cls(calling_agent=DemoAgent()),
                ToolResult(
                    tool_name=cls.TOOL_NAME,
                    success=True,
                    output="The first step is: ...",
                ),
            ),
        ]
