# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
import shlex
import asyncio
import logging

from typing import ClassVar
from pydantic import Field, PrivateAttr

from .base_tool import BaseTool
from ..types.tool_types import ToolResult
from ..types.agent_types import AgentInterface

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ExecuteCommand(BaseTool):
    """Tool for executing shell commands that are guaranteed to return."""

    # Class variables required by BaseTool
    TOOL_NAME: ClassVar[str] = "execute_command"
    TOOL_DESCRIPTION: ClassVar[
        str
    ] = """
Execute a shell command that is guaranteed to return within a specified timeout.

This tool is specifically for commands that will complete and return control, not for long-running services or background processes.

Commands that run indefinitely (like servers) are not supported and will be rejected immediately.
Use appropriate service management tools for long-running processes instead.

Note that commands are run on a Fedora sandbox. Use the system pip installation, dnf and pnpm.

Example usage:
- compiling or running commands
- running tests
- cURL or wget to download resources from the internet
"""

    # Command specification
    intent: str = Field(
        ...,
        description="A concise description of what you're trying to achieve by running this command. This should be informative enough for a supervisor to accurately assess the execution's success.",
        min_length=1,
    )
    directory_in_which_to_run_command: str = Field(
        default="/home/agent/workdir",
        description="The directory from which to execute the command",
    )
    command: str = Field(
        ...,
        description="A single or multi-line bash command to be run in the terminal.",
        min_length=1,
    )
    command_returns: bool = Field(
        ...,
        description="Whether the command is expected to return (True) or run indefinitely like a server (False). Note that any command with command_returns=False will be rejected.",
    )
    generous_expected_duration: float = Field(
        ...,
        description="A generous estimate of the expected time (in seconds) that command will take to return. Must be between 5.0 and 1800s (30 mins).",
        ge=5.0,
        le=1800.0,
    )

    # Private attributes for internal state
    _process: asyncio.subprocess.Process | None = PrivateAttr(default=None)

    def __init__(self, calling_agent: AgentInterface, **data):
        super().__init__(calling_agent=calling_agent, **data)

    async def prepare_command(self) -> str:
        """
        Prepare the command for execution by encoding it safely and adding any necessary wrapper logic.
        """
        script_lines = [
            "#!/bin/bash",
            "set -e",  # Exit on any error
            f"cd {shlex.quote(self.directory_in_which_to_run_command)}" if self.directory_in_which_to_run_command is not None else "",
            self.command,
        ]
        return "\n".join(filter(None, script_lines))

    async def run(self) -> ToolResult:
        """Execute the command if it satisfies the return constraint."""

        # First, enforce the returnable command constraint
        if not self.command_returns:
            return ToolResult(
                tool_name=self.TOOL_NAME,
                success=False,
                errors=(
                    "This tool only supports commands that return. "
                    "The command was marked as non-returning (command_returns=False). "
                    "Use appropriate service management tools for long-running processes instead."
                ),
            )

        try:
            prepared_command = await self.prepare_command()

            # Create subprocess
            self._process = await asyncio.create_subprocess_shell(
                prepared_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Wait for completion with timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    self._process.communicate(), timeout=self.generous_expected_duration
                )

                output_str = "The shell output was:\n"
                output_str += f"<stdout>{stdout.decode()}</stdout>\n"
                output_str += f"<stderr>{stderr.decode()}</stderr>\n"
                output_str += f"<exit_code>{self._process.returncode}</exit_code>\n"

                return ToolResult(
                    tool_name=self.TOOL_NAME,
                    success=True,
                    output=output_str,
                )

            except asyncio.TimeoutError:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._process.kill()  # Force kill if terminate didn't work

                # TODO: capture and return output so far...

                return ToolResult(
                    tool_name=self.TOOL_NAME,
                    success=False,
                    errors=f"Command timed out after {self.generous_expected_duration} seconds",
                )

        except Exception as e:
            return ToolResult(
                tool_name=self.TOOL_NAME,
                success=False,
                errors=f"Error executing command: {str(e)}",
            )

    @classmethod
    def generate_examples(cls) -> list[tuple["BaseTool", ToolResult]]:
        """Generate example uses of the tool with their expected outputs."""
        from ..agents.implementations import DemoAgent

        examples = [
            # Example 1: Simple command that returns
            (
                cls(
                    calling_agent=DemoAgent(),
                    intent="List files in the current directory",
                    command="ls -la",
                    command_returns=True,
                    generous_expected_duration=5.0,
                ),
                ToolResult(
                    tool_name=cls.TOOL_NAME,
                    success=True,
                    output={
                        "stdout": "total 0\ndrwxr-xr-x 2 user user 40 Jan 1 12:00 .",
                        "stderr": "",
                        "exit_code": 0,
                    },
                ),
            ),
            # Example 2: Non-returning command (rejected)
            (
                cls(
                    calling_agent=DemoAgent(),
                    intent="Start a development server on port 3000",
                    directory_in_which_to_run_command="/home/agent/workdir/web_project",
                    command="python -m http.server 3000",
                    command_returns=False,
                    generous_expected_duration=10.0,
                ),
                ToolResult(
                    tool_name=cls.TOOL_NAME,
                    success=False,
                    errors=(
                        "This tool only supports commands that return. "
                        "The command was marked as non-returning (command_returns=False). "
                        "Use appropriate service management tools for long-running processes instead."
                    ),
                ),
            ),
            # Example 3: Long but returning command
            (
                cls(
                    calling_agent=DemoAgent(),
                    intent="Run a comprehensive test suite",
                    command="pytest -v --cov=./",
                    command_returns=True,
                    generous_expected_duration=300.0,
                ),
                ToolResult(
                    tool_name=cls.TOOL_NAME,
                    success=True,
                    output={
                        "stdout": "===== test session starts =====\n...",
                        "stderr": "",
                        "exit_code": 0,
                    },
                ),
            ),
        ]
        return examples
