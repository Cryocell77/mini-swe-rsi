# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""Main API interface for LLM interactions."""

import os
import logging

from uuid import uuid4
from typing import List, Optional, Union, AsyncGenerator, Type
from google import genai
from openai import AsyncOpenAI
from dataclasses import dataclass
from pydantic import BaseModel

from .base import (
    Message,
    Completion,
    CompletionChunk,
)
from .metering import token_meter, llm_call_counter
from .providers import (
    AnthropicProvider,
    OpenAIProvider,
    DeepSeekProvider,
    FireworksProvider,
    GoogleProvider,
    GoogleRESTProvider,
    GoogleOAIProvider,
    VertexProvider,
)
from ..types.common import ArgFormat
from ..utils.stop_tokens import TOOL_STOP_TOKEN, AGENT_STOP_TOKEN
from ..utils.parsing import extract_before_last, extract_after_last
from ..types.llm_types import Provider, FCI, Model, MODEL_FAILOVER_MAP, ReasoningEffort, TextContent, ToolCallContent
from ..types.agent_types import AgentInterface
from ..types.tool_types import ToolInterface

logger = logging.getLogger(__name__)


@dataclass
class DummyClient:
    """Dummy client that just holds an API key."""

    api_key: str


class ModelProvider:
    """Interface for LLM providers with API key management."""

    _clients = {}
    _providers = {}

    @classmethod
    def _get_anthropic_client(cls):
        """Get or create an Anthropic client with API key from env var."""
        if Provider.ANTHROPIC not in cls._clients:
            from anthropic import AsyncAnthropic

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("Anthropic API key not found in settings")
            cls._clients[Provider.ANTHROPIC] = AsyncAnthropic(api_key=api_key)
        return cls._clients[Provider.ANTHROPIC]

    @classmethod
    def _get_openai_client(cls):
        """Get or create an OpenAI client with API key from env var."""
        if Provider.OPENAI not in cls._clients:

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OpenAI API key not found in settings")
            cls._clients[Provider.OPENAI] = AsyncOpenAI(api_key=api_key)
        return cls._clients[Provider.OPENAI]

    @classmethod
    def _get_deepseek_client(cls):
        """Get or create a DeepSeek client with API key from env var."""
        if Provider.DEEPSEEK not in cls._clients:

            api_key = os.environ.get("DEEPSEEK_API_KEY")
            if not api_key:
                raise ValueError("DeepSeek API key not found in settings")
            cls._clients[Provider.DEEPSEEK] = DummyClient(api_key=api_key)
        return cls._clients[Provider.DEEPSEEK]

    @classmethod
    def _get_fireworks_client(cls):
        """Get or create a Fireworks client with API key from env var."""
        if Provider.FIREWORKS not in cls._clients:
            api_key = os.environ.get("FIREWORKS_AI_API_KEY")
            if not api_key:
                raise ValueError("Fireworks API key not found in settings")
            cls._clients[Provider.FIREWORKS] = DummyClient(api_key=api_key)
        return cls._clients[Provider.FIREWORKS]

    @classmethod
    def _get_gemini_client(cls):
        """Get or create a Google gemini client with API key from env var."""
        if Provider.GOOGLE not in cls._clients:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("Gemini API key not found in settings")
            cls._clients[Provider.GOOGLE] = genai.Client(api_key=api_key)
        return cls._clients[Provider.GOOGLE]

    @classmethod
    def _get_gemini_oai_client(cls):
        """Get or create an OpenAI SDK-compatible Gemini client with API key from env var."""
        if Provider.GOOGLE_OAI not in cls._clients:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("Gemini API key not found in settings")
            cls._clients[Provider.GOOGLE_OAI] = AsyncOpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=api_key
            )
        return cls._clients[Provider.GOOGLE_OAI]

    @classmethod
    def _get_vertex_client(cls):
        """Get or create a Vertex AI client for Anthropic models."""
        if Provider.VERTEX not in cls._clients:
            from anthropic import AsyncAnthropicVertex

            project_id = os.environ.get("VERTEX_PROJECT_ID")
            if not project_id:
                raise ValueError("Vertex project ID not found in environment variables")

            # Default region, can be made configurable if needed
            region = os.environ.get("VERTEX_REGION", "us-east5")

            cls._clients[Provider.VERTEX] = AsyncAnthropicVertex(
                project_id=project_id,
                region=region
            )
        return cls._clients[Provider.VERTEX]

    @classmethod
    def get_provider(cls, provider: Provider):
        """Get the appropriate provider implementation."""
        if provider not in cls._providers:
            match provider:
                case Provider.ANTHROPIC:
                    client = cls._get_anthropic_client()
                    cls._providers[provider] = AnthropicProvider(client)
                case Provider.OPENAI:
                    client = cls._get_openai_client()
                    cls._providers[provider] = OpenAIProvider(client)
                case Provider.DEEPSEEK:
                    client = cls._get_deepseek_client()
                    cls._providers[provider] = DeepSeekProvider(client)
                case Provider.FIREWORKS:
                    client = cls._get_fireworks_client()
                    cls._providers[provider] = FireworksProvider(client)
                case Provider.GOOGLE:
                    client = cls._get_gemini_client()
                    cls._providers[provider] = GoogleProvider(client)
                case Provider.GOOGLE_OAI:
                    client = cls._get_gemini_oai_client()
                    cls._providers[provider] = GoogleOAIProvider(client)
                case Provider.GOOGLE_REST:
                    key = os.environ.get("GEMINI_API_KEY")
                    cls._providers[provider] = GoogleRESTProvider(api_key=key)
                case Provider.VERTEX:
                    client = cls._get_vertex_client()
                    cls._providers[provider] = VertexProvider(client)
                case _:
                    raise ValueError(f"Unexpected provider: {provider}")
        return cls._providers[provider]


def get_tool_documentation(
    available_tools: list[Type[AgentInterface | ToolInterface]] | None,
    arg_format: ArgFormat,
    plain: bool = False,
) -> str:
    """
    Generates the documentation and few-shot usage examples for tools and
    agents for the unconstrained tool calling mode.

    If `plain`, then we adapt the documentation to merely liist usage examples
    in a less structured format for constrained tool use and generation; so as
    to not interfere with the provider-specific constrained tool calling
    implementation.
    """
    if available_tools is None:
        return ""

    # To prevent circular imports. TODO: structure the modules to better
    # separate concerns
    from ..tools.base_tool import get_tool_instructions
    from ..agents.agent_calling import get_agent_instructions

    if plain:
        tool_instructions = ["\n\n# Tool Documentation"]
        agent_instructions = ["\n\n# Agent Documentation"]
    else:
        tool_instructions = [
            "\n\n# Tool Instructions",
            get_tool_instructions(arg_format),
        ]
        agent_instructions = [
            "\n\n# Agent Instructions",
            get_agent_instructions(arg_format),
        ]

    for tool_class in available_tools:
        if hasattr(tool_class, "TOOL_NAME"):
            # tool_class is a BaseTool
            if plain:
                tool_instructions.append(tool_class.to_plain_prompt_format(arg_format))
            else:
                tool_instructions.append(tool_class.to_prompt_format(arg_format))
        elif hasattr(tool_class, "AGENT_NAME"):
            # tool_class is a BaseAgent
            if plain:
                agent_instructions.append(tool_class.to_plain_prompt_format(arg_format))
            else:
                agent_instructions.append(tool_class.to_prompt_format(arg_format))

    result = ""
    if len(tool_instructions) > 2:
        result += "\n".join(tool_instructions)
    if len(agent_instructions) > 2:
        result += "\n".join(agent_instructions)

    return result


async def create_completion(
    messages: List[Message],
    model: Model = Model.SONNET_37,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    stop: str | list[str] | None = None,
    top_p: float = 1.0,
    max_continuations: int | None = None,
    reasoning_effort: ReasoningEffort | None = None,
    available_tools: list[Type[ToolInterface] | Type[AgentInterface]] | None = None,
    num_completions: int = 1,
    # structure: type[BaseModel] | None = None,
    # TODO: add fci_override arg here?
    max_retries: int = 3,
) -> Completion:
    """Unified endpoint to create a (non-streaming) LLM completion using the
    appropriate provider with retry and failover.

    Args:
        messages: The list of Messages to send to the model
        model: The model to use
        temperature: Sampling temperature (note, some reasoning models don't support this)
        max_tokens: Max tokens to generate (defaults to model max)
        stop: A stop sequence or list of stop sequences
        top_p: For top-p sampling
        max_continuations: for extended generations, the number of continuations to allow
        reasoning_effort: for reasoning models, the reasoning effort / budget
        available_tools: the list of available tools to call (either via unconstrained or constrained generation, depending on the model's FCI)
        num_completions: The number of completions to return. Note that this is incompatible with continuations.
        structure: invokes structured generation
        max_retries: number of times to retry

    Note:
        If `structure' is provided, then max_continuations will be set to 1 (no
        continuations)

    Returns:
        A completion object
    """

    async def try_completion(current_model: Model) -> Completion:
        provider = ModelProvider.get_provider(current_model.provider)
        custom_tool_calling = (
            available_tools is not None and current_model.fci == FCI.UNCONSTRAINED
        )
        # if structure or num_completions > 1:
        #     max_continuations = 1

        current_stop = set(
            [stop] if isinstance(stop, str) else ([] if stop is None else stop)
        )
        if custom_tool_calling:
            # Add in stop tokens for unconstrained tool / agent calling
            current_stop.update((TOOL_STOP_TOKEN, AGENT_STOP_TOKEN))

            # Add tool calling instructions to the system prompt (if one is
            # provided), or create one ourselves just for this
            tools = available_tools if available_tools else []
            # Important: the list of available tools is constructed based of set unions, which are un-ordered.
            # To avoid breaking the cache, we sort them here
            sorted_tools = sorted(tools, key=lambda x: x.__name__)
            # Skip 'ephemeral' tools (tools that only last a few turn) to avoid
            # breaking the cache when they are added / removed
            filtered_tools = [t for t in sorted_tools if not getattr(t, "EPHEMERAL", False)]
            instr = get_tool_documentation(filtered_tools, model.arg_format)
            sys_messages = [m for m in messages if m.role == "system"]
            sys_message = (
                sys_messages[-1]
                if len(sys_messages)
                else Message(role="system", content=[])
            )
            # Append the tool documentation to the end of the system prompt.
            sys_message.content.append(TextContent(text=instr))

        current_stop = list(current_stop)

        # Use continuation mode if specified
        if max_continuations is not None:
            completion = await provider.create_continuation_completion(
                messages=messages,
                model=current_model,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=current_stop,
                top_p=top_p,
                max_continuations=max_continuations,
                reasoning_effort=reasoning_effort,
                available_tools=available_tools,
            )
        else:
            # Default to single completion
            completion = await provider.create_completion(
                messages=messages,
                model=current_model,
                temperature=temperature,
                max_tokens=max_tokens,
                stop=current_stop,
                top_p=top_p,
                reasoning_effort=reasoning_effort,
                available_tools=available_tools,
                num_completions=num_completions,
            )  # Check for empty response
        if not completion:
            raise ValueError("No completion returned from provider")

        # Handle custom tool and agent invocations
        if custom_tool_calling:
            from ..tools.base_tool import parse_tool_content
            from ..agents.agent_calling import parse_agent_content

            last_content = completion.content[-1].text.rstrip()
            if last_content.endswith("</TOOL_ARGS>"):
                last_content += "\n" + TOOL_STOP_TOKEN
                tool_name, tool_args, parse_warnings = await parse_tool_content(
                    last_content, model.arg_format
                )
                assistant_cot = extract_before_last(
                    last_content, "<TOOL_CALL>"
                ).rstrip()
                # Extract the tool call text
                call_block = extract_after_last(
                    last_content, "<TOOL_CALL>", keep_pattern=True
                )
                if assistant_cot == "":
                    completion.content.pop()
                else:
                    completion.content[-1].text = assistant_cot

                # Add the separate ToolCall content type to create the ToolCall event
                completion.content.append(
                    ToolCallContent(
                        call_id=f"custom_tool_call_{uuid4().hex[:8]}",
                        tool_name=tool_name or "undefined",
                        tool_args=tool_args or dict(),
                        call_type=FCI.UNCONSTRAINED,
                        parse_errors=parse_warnings,
                    )
                )
            elif last_content.endswith("</AGENT_ARGS>"):
                last_content += "\n" + AGENT_STOP_TOKEN
                agent_name, agent_args, parse_warnings = await parse_agent_content(
                    last_content, model.arg_format
                )
                assistant_cot = extract_before_last(
                    last_content, "<AGENT_CALL>"
                ).rstrip()
                # Trim agent call from last assistant response since this will
                # be represented in the AgentCall event
                if assistant_cot == "":
                    completion.content.pop()
                else:
                    completion.content[-1].text = assistant_cot
                # Add the separate ToolCall content to create the AgentCall event
                completion.content.append(
                    ToolCallContent(
                        call_id=f"custom_agent_call_{uuid4().hex[:8]}",
                        tool_name=agent_name or "undefined",
                        tool_args=agent_args or dict(),
                        call_type=FCI.UNCONSTRAINED,
                        parse_errors=parse_warnings,
                    )
                )

        return completion

    # Try original model twice
    for attempt in range(max_retries):
        try:
            completion = await try_completion(model)
            token_meter[model] += completion.usage
            llm_call_counter.count_new_call()
            return completion
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed with model {model.id}: {e}")
            if attempt == 1:  # Last attempt with original model
                break

    # Try failover model if available
    failover_model = MODEL_FAILOVER_MAP.get(model)
    if failover_model:
        try:
            logger.info(f"Attempting failover to {failover_model.id}")
            completion = await try_completion(failover_model)
            token_meter[failover_model] += completion.usage
            llm_call_counter.count_new_call()
            return completion
        except Exception as e:
            logger.error(f"Failover to {failover_model.id} failed: {e}")

    # If all attempts fail
    raise Exception(f"All completion attempts failed for {model.id} and failover")


async def create_streaming_completion(
    messages: List[Message],
    model: Model = Model.SONNET_37,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    stop: Optional[Union[str, List[str]]] = None,
    top_p: float = 1.0,
    max_continuations: int = 4,
    reasoning_effort: ReasoningEffort | None = None,
    available_tools: list[Type[BaseModel]] | None = None,
) -> AsyncGenerator[CompletionChunk, None]:
    """Create a streaming completion using the appropriate provider.

    Args:
        messages: List[Message] - Messages to send to the model
        model: Model - The model to use (defaults to Claude 3.5 Sonnet)
        temperature: float - Temperature parameter for diversity
        max_tokens: Optional[int] - Maximum tokens to generate
        stop: Optional[Union[str, List[str]]] - Optional stop sequence(s)
        top_p: float - Top-p sampling parameter
        max_continuations: Optional[int] - Maximum number of continuations to allow
            when hitting max tokens (default: None, no continuation)
        reasoning_effort: for reasoning models, express the reasoning effort / budget
        available_tools: the list of available tools to call via constrained generation
    """
    try:
        provider = ModelProvider.get_provider(model.provider)

        async for chunk in provider.create_streaming_continuation_completion(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
            top_p=top_p,
            max_continuations=max_continuations,
            reasoning_effort=reasoning_effort,
            available_tools=available_tools,
        ):
            chunk: CompletionChunk

            # If we're handling the last chunk (which has the aggregated usage
            # statistics), then append to the token metering information
            if chunk.usage and chunk.content == "":
                token_meter[model] += chunk.usage

            yield chunk
    except Exception as e:
        logger.error(f"Error creating streaming completion: {e}")
        raise
