# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""
Agent for comprehensive archive analysis across agent iterations, benchmarks, and problems.
"""

from pathlib import Path
from pydantic import Field

from ..base_agent import BaseAgent
from ...config import settings
from ...utils.archive_analysis import ArchiveAnalyzer
from ...tools.directory_tools import ViewDirectory
from ...tools.file_tools import OpenFile, CloseFile
from ...tools.archive_tools import (
    WorstProblems,
    BestProblems,
    CompareIterations,
)
from ...utils.metrics import make_random_agent_metrics
from .reasoner import ReasoningAgent
from ...types.agent_types import AgentStatus, AgentResult


class ArchiveExplorer(BaseAgent):
    """
    Specialized agent for comprehensive archive analysis.
    """

    AGENT_NAME = "archive_explorer"
    AGENT_DESCRIPTION = """
When asked to improve your codebase in a meta-agent update task, use this tool FIRST to thoroughly explore the archive of benchmark runs and historical agent iteration performance, to get some feedback about what we should try improving next.

For self-improvement runs, you will have the following directory tree mounted:

Archive Structure
/home/agent/archive/
├── agent_{i}/
│   ├── agent_code/
│   │   ├── ... (agent implementation)
│   │   ├── agent_change_log.md (log of agent changes at that agent iteration in time)
│   │   └── description.txt (agent changes/features)
│   └── benchmarks/
│       └── {bench_name}/
│           ├── perf.json (benchmark summary)
│           ├── results.jsonl (per-problem results)
│           └── traces/
│               └── {problem_id}/
│                   ├── answer.txt (raw answer)
│                   ├── summary.txt (performance summary)
│                   ├── trace.txt (full trace)
│                   └── execution_tree.txt (concise trace)

This agent provides utilities to help efficiently sift through the benchmark results, efficiently performing:
1. Multi-level analysis (overview, iteration, benchmark, problem)
2. Performance tracking and comparison
3. Pattern identification and analysis
4. Detailed trace examination
5. Success and failure analysis

You may also optionally specify a focus to this agent to focus on a specific aspect of agent performance. It is recommended that you first call this agent without a specific focus to get a broad understanding of past agent performance. Then, if you wish to more closely inspect any particular aspect of agent performance, you may provide a focus.
"""

    SYSTEM_PROMPT = """You are an expert archive analyst for improving agentic systems. You have been given an archive of past agent iterations, and the performance of each of those agent iterations on a number of benchmarks.

To analyze it efficiently, you have also been provided with some specialised tools, which you should primarily use. In the event that some agent functionality is not present, you may manually inspect the archive using your file and execute_command tools, although this is discouraged.
"""

    # Available tools
    AVAILABLE_TOOLS = {
        ViewDirectory,
        OpenFile,
        CloseFile,
        BestProblems,
        WorstProblems,
        CompareIterations,
    }

    # This is a leaf agent
    AVAILABLE_AGENTS = {ReasoningAgent}

    HAS_FILEVIEW = True
    MODEL = settings.MODEL

    # Agent parameters
    analysis_focus: str = Field(
        default="What should be implemented next in order to maximise performance or utility?",
        description="The analytical focus for exploration",
    )

    def __init__(self, parent: BaseAgent | None = None, workdir: Path | None = None, logdir: Path | None = None, debug_mode: bool = False, **data):
        super().__init__(parent=parent, workdir=workdir, logdir=logdir, debug_mode=debug_mode, **data)

    async def construct_core_prompt(self) -> str:
        """Construct the core prompt for archive analysis."""
        # Initialize analyzer
        analyzer = ArchiveAnalyzer("/home/agent/archive")
        analysis = analyzer.analyze_run_formatted()

        prompt = f"""You are inspecting an archive of runs for a self-improving agent system. Use the analysis tools made available to you to comprehensively analyze the agent benchmark archive.

Here is the archive structure:

Archive Structure
/home/agent/archive/
├── agent_<i>/
│   ├── agent_code/
│   │   ├── ... (agent implementation)
│   │   ├── agent_change_log.md (log of agent changes at that agent iteration in time)
│   │   └── description.txt (agent changes/features)
│   └── benchmarks/
│       └── <bench_name>/
│           ├── perf.json (benchmark summary)
│           ├── results.jsonl (per-problem results)
│           └── traces/
│               └── <problem_id>/
│                   ├── answer.txt (raw answer)
│                   ├── summary.txt (performance summary)
│                   ├── trace.txt (full trace)
│                   └── execution_tree.txt (concise trace)


Your focus should be on:

{self.analysis_focus}

Here is a high-level overview the agent iterations and their benchmark performance:

{analysis}
"""
        prompt += """
You should generally first call your archive analysis tools to get summary statistics of the runs.

Remember to look at the description.txt and agent_change_log.md in each of the runs of interest to gain context about a particular agent iteration. The README file will also provide context for the project description, motivation and philosophy.

Use your reasoning agent if necessary to reason over your findings from the archive exploration.
"""
        #         prompt += """
        # Your procedure should be as follows, strictly in this order, writing each step number out as you go:
        #
        # 1. You should first call your archive analysis tools to get summary statistics of the runs.
        #
        # 2. You should also inspect the description.txt file in each agent of interest to identify what has been tried before. You may also look in the agent_change_log.md in each directory to get an idea of what was tried before. You MUST also open the README file to see the project description, motivation and philosophy.
        #
        # 3. Having viewed the project documentation and README, you should then call your reasoning agent to help come up with an interesting, tasteful and non-trivial and impactful new modification to make to the agent system to help improve it.
        #
        # 4. Verify that the suggestion from the reasoner is indeed new, interesting and isn't already implemented in the current codebase. From what you know about the project, would you say it exhibits good taste? You should also inspect the historical agents, their descriptions.txt and the agent_change_log.md files to verify that this hasn't been tried previously and ruled out. If you find that the idea is no good, return to 3. and call the reasoning agent again.
        #
        # Return a detailed description of the result of this analysis to your calling agent.
        # """

        return prompt

    @classmethod
    def generate_examples(cls) -> list[tuple["BaseAgent", AgentResult]]:
        """Generate example uses of the tool with their expected outputs"""
        examples = [
            # Example 1: Analysis focused on reasoning capabilities
            (
                cls(
                    analysis_focus="What components should be added to improve the agent's reasoning on complex tasks"
                ),
                AgentResult(
                    agent_name=cls.AGENT_NAME,
                    status=AgentStatus.SUCCESS,
                    result="""Archive Analysis Summary:

UNSUCCESSFUL APPROACHES:
Agent Iteration 5:
- Implemented freeform chain-of-thought without structure
- Used generic task decomposition without validation steps
- Attempted parallel solution exploration without coordination
Key Issue: Lack of systematic verification between reasoning steps

SUCCESSFUL APPROACHES:
Agent Iteration 8:
- Structured reasoning framework with explicit verification
- Tree-based problem decomposition with dependency tracking
- Implementation of solution validation gates
Success Factor: Systematic breakdown with verification at each step

RECOMMENDED NEXT FEATURE:
Implement a "Verified Reasoning Pipeline":
- Add checkpoint system between reasoning steps
- Include explicit dependency tracking between sub-solutions
- Implement rollback capability when sub-steps fail validation

This will build on the successful approaches from Iteration 8 while adding
stronger guarantees for solution coherence. Implementation should focus on
making the verification steps explicit and programmatically enforced.""",
                    metrics=make_random_agent_metrics(
                        tools_enabled=True, agents_enabled=False
                    ),
                ),
            ),
            # Example 2: Analysis focused on tool usage optimization
            (
                cls(
                    analysis_focus="How to improve the agent's effectiveness in using available tools"
                ),
                AgentResult(
                    agent_name=cls.AGENT_NAME,
                    status=AgentStatus.SUCCESS,
                    result="""Archive Analysis Summary:

UNSUCCESSFUL APPROACHES:
Agent Iteration 3:
- Random tool exploration without planning
- No caching of tool results leading to redundant calls
- Attempted but failed to implement tool output validation
Key Issue: Inefficient and unreliable tool usage patterns

SUCCESSFUL APPROACHES:
Agent Iteration 6:
- Tool selection based on explicit task requirements
- Result caching with validity tracking
- Implemented retry logic for unreliable tools
Success Factor: Thoughtful tool orchestration with fallbacks

RECOMMENDED NEXT FEATURE:
Implement "Smart Tool Router":
- Add tool outcome prediction before execution
- Implement result caching with validity timeouts
- Create tool chain templates for common operations

This builds on Iteration 6's success while adding better prediction
and coordination of tool usage patterns.""",
                    metrics=make_random_agent_metrics(
                        tools_enabled=True, agents_enabled=False
                    ),
                ),
            ),
            # Example 3: Analysis focused on self-improvement mechanisms
            (
                cls(
                    analysis_focus="How to enhance the agent's ability to improve its own code and systems"
                ),
                AgentResult(
                    agent_name=cls.AGENT_NAME,
                    status=AgentStatus.SUCCESS,
                    result="""Archive Analysis Summary:

UNSUCCESSFUL APPROACHES:
Agent Iteration 4:
- Attempted wholesale system rewrites
- Made simultaneous changes across multiple components
- Lacked clear criteria for improvement validation
Key Issue: Changes were too broad and hard to validate

SUCCESSFUL APPROACHES:
Agent Iteration 7:
- Focused on single-component improvements
- Implemented before/after performance testing
- Maintained compatibility with existing systems
Success Factor: Incremental, validated improvements

RECOMMENDED NEXT FEATURE:
Implement "Component Test Framework":
- Add automated performance regression testing
- Create component isolation mechanism
- Implement gradual feature rollout system

This will enable safer and more reliable self-modifications while
building on the incremental approach that worked in Iteration 7.""",
                    metrics=make_random_agent_metrics(
                        tools_enabled=True, agents_enabled=False
                    ),
                ),
            ),
        ]
        return examples
