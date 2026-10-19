# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""DeepSeek-specific LLM provider implementation."""

import json
import logging
import tiktoken

from typing import List, Optional, Union, AsyncGenerator, Any
from openai import AsyncOpenAI
from datetime import datetime
from pydantic import BaseModel
from openai.types.chat import ChatCompletionMessage, ChatCompletionMessageToolCall
from openai.types.chat.chat_completion_message_tool_call import Function

from ..base import (
    Message,
    Completion,
    CompletionChunk,
    TimingInfo,
)
from .base_provider import BaseProvider
from ...types.llm_types import TokenUsage, Model, StopReason, ReasoningEffort, TextContent, ReasoningContent, ToolCallContent, ToolResultContent, FCI

logger = logging.getLogger(__name__)


class DeepSeekProvider(BaseProvider):
    """Provider implementation for DeepSeek's models."""

    def __init__(self, client):
        self.api_key = client.api_key
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url="https://api.deepseek.com/beta",
        )
        self.tokenizer = tiktoken.encoding_for_model("gpt-4")

    def map_stop_reason(self, response: Any) -> tuple[StopReason, Optional[str]]:
        """Map DeepSeek finish reasons to our standard format."""
        # Get finish reason from response
        raw = getattr(response, "_raw_response", {})
        finish_reason = raw.get("finish_reason")

        if not finish_reason and hasattr(response, "choices") and response.choices:
            finish_reason = response.choices[0].finish_reason

        # Map to standard format
        if finish_reason in ("length", "insufficient_system_resource"):
            return StopReason.LENGTH, None
        elif finish_reason == "error":
            return StopReason.ERROR, None
        else:  # 'stop' or others
            return StopReason.COMPLETE, None

    def _create_token_usage(self, response: Any) -> TokenUsage:
        """Create TokenUsage object from response."""
        usage = getattr(response, "usage", None)
        if not usage:
            logger.warning("Missing usgae information from DeepSeek API response. Setting to 0")
            return TokenUsage()

        try:
            return TokenUsage(
                uncached_prompt_tokens=getattr(usage, "prompt_tokens", 0),
                cache_write_prompt_tokens=(
                    usage.prompt_cache_miss_tokens
                    if hasattr(usage, "prompt_cache_miss_tokens")
                    else 0
                ),  # This is not 100% accurate
                cached_prompt_tokens=(
                    usage.prompt_cache_hit_tokens
                    if hasattr(usage, "prompt_cache_hit_tokens")
                    else 0
                ),
                completion_tokens=getattr(usage, "completion_tokens", 0),
            )
        except Exception as e:
            logger.warning(f"Failed to create token usage: {e}. Setting to 0")
            return TokenUsage()

    def pydantic_to_native_tool(self, tool) -> dict:
        if hasattr(tool, "TOOL_NAME"):
            return {
                "type": "function",
                "function": {
                    "name": tool.TOOL_NAME,
                    "description": tool.TOOL_DESCRIPTION,
                    "parameters": tool.model_json_schema()
                }
            }
        elif hasattr(tool, "AGENT_NAME"):
            return {
                "type": "function",
                "function": {
                    "name": tool.AGENT_NAME,
                    "description": tool.AGENT_DESCRIPTION,
                    "parameters": tool.model_json_schema()
                }
            }
        raise ValueError(f"provided tool {tool} is neither BaseTool nor BaseAgent")

    def _prepare_messages(self, messages: list[Message]) -> list[dict]:
        # Prepare the messages from our format to OpenAI compatible
        ds_messages = []
        for msg in messages:
            if msg.role == "assistant":
                # Handle assistant messages and allowed / expected blocks
                msg_content = ""
                tool_calls = []
                for block in msg.content:
                    # We only expecte TextContent and ToolCallContent
                    # Reasoning is omitted in DS, and we don't expect ToolResponseContent
                    if isinstance(block, TextContent):
                        msg_content += block.text
                    elif isinstance(block, ToolCallContent):
                        tool_calls.append(ChatCompletionMessageToolCall(
                            id=block.call_id,
                            function=Function(
                                arguments=json.dumps(block.tool_args),
                                name=block.tool_name,
                            ),
                            type="function",
                        ))
                # TODO: inspect the response from DeepSeek and emulate this;
                # don't use OpenAI's types here
                ds_messages.append(ChatCompletionMessage(
                    content=msg_content,
                    role="assistant",
                    tool_calls=tool_calls,
                ))
            else:
                msg_content = ""
                for block in msg.content:
                    if isinstance(block, TextContent):
                        msg_content += block.text
                    elif isinstance(block, ToolResultContent):
                        # Append what we have so far
                        if msg_content != "":
                            ds_messages.append({"role": msg.role, "content": msg_content})
                            msg_content = ""
                        ds_messages.append({"role": "tool", "tool_call_id": block.call_id, "content": block.content})
                if msg_content != "":
                    ds_messages.append({"role": msg.role, "content": msg_content})

        return ds_messages

    async def create_completion(
        self,
        messages: List[Message],
        model: Model,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stop: Optional[Union[str, List[str]]] = None,
        top_p: float = 1.0,
        reasoning_effort: ReasoningEffort | None = None,
        available_tools: list[type[BaseModel]] | None = None,
        num_completions: int = 1,
    ) -> Completion:
        start_time = datetime.now()

        api_messages = self._prepare_messages(messages)

        if api_messages[-1]["role"] == "assistant":
            api_messages[-1]["prefix"] = True

        args = {
            "messages": api_messages,
            "model": model.id,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens or model.max_output_tokens,
        }

        if stop:
            args["stop"] = [stop] if isinstance(stop, str) else stop

        if available_tools:
            args["tools"] = [self.pydantic_to_native_tool(t) for t in available_tools]

        response = await self.client.chat.completions.create(**args)
        end_time = datetime.now()

        # Create token usage and timing info
        token_usage = self._create_token_usage(response)
        duration = end_time - start_time

        timing_info = TimingInfo(
            start_time=start_time,
            end_time=end_time,
            total_duration=duration,
            first_token_time=end_time,
            time_to_first_token=duration.total_seconds(),
            tokens_per_second=(
                token_usage.completion_tokens / duration.total_seconds()
                if duration.total_seconds() > 0 and token_usage
                else None
            ),
        )

        # Map stop reason
        stop_reason, stop_sequence = self.map_stop_reason(response)

        message = response.choices[0].message

        response_content = []
        # First, get any reasoning content
        if hasattr(message, "reasoning_content"):
            response_content.append(ReasoningContent(text=message.reasoning_content))
        # Then, get any assistant message
        if message.content:
            response_content.append(TextContent(text=message.content))
        # Finally get any tool / function calls
        if message.tool_calls:
            for tc in message.tool_calls:
                response_content.append(ToolCallContent(
                    call_id=tc.id,
                    tool_name=tc.function.name,
                    tool_args=json.loads(tc.function.arguments),
                    call_type=FCI.CONSTRAINED,
                ))

        return Completion(
            id=response.id,
            content=response_content,
            model=model,
            usage=token_usage,
            timing=timing_info,
            stop_reason=stop_reason,
            stop_sequence=stop_sequence,
            raw_response={
                "finish_reason": response.choices[0].finish_reason,
                "created": response.created,
                "model": response.model,
                "system_fingerprint": getattr(response, "system_fingerprint", None),
            },
        )

    async def create_streaming_completion(
        self,
        messages: List[Message],
        model: Model,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stop: Optional[Union[str, List[str]]] = None,
        top_p: float = 1.0,
        reasoning_effort: ReasoningEffort | None = None,
        available_tools: list[BaseModel] | None = None,
    ) -> AsyncGenerator[CompletionChunk, None]:
        """Create a streaming completion."""
        # TODO: fix this.
        start_time = datetime.now()
        first_token_time = None
        accumulated_content = []
        final_usage = None
        current_stop = None

        # Get accurate prompt token count
        prompt_tokens = sum(
            len(
                self.tokenizer.encode(
                    msg.content
                    if isinstance(msg.content, str)
                    else "\n".join(msg.content)
                )
            )
            for msg in messages
        )

        args = {
            "messages": [
                {
                    "role": msg.role,
                    "content": (
                        msg.content
                        if isinstance(msg.content, str)
                        else "\n".join(msg.content)
                    ),
                    **({"name": msg.name} if msg.name else {}),
                }
                for msg in messages
            ],
            "model": model.id,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens or model.max_output_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        if stop:
            args["stop"] = [stop] if isinstance(stop, str) else stop

        async for chunk in await self.client.chat.completions.create(**args):
            current_time = datetime.now()

            # Handle final usage info chunk
            if not chunk.choices and hasattr(chunk, "usage"):
                final_usage = self._create_token_usage(chunk)
                continue

            if not chunk.choices:
                continue

            # Handle content chunk
            delta = chunk.choices[0].delta

            # Update stop reason if finish reason present
            if chunk.choices[0].finish_reason is not None:
                stop_reason, _ = self.map_stop_reason(chunk)
                current_stop = stop_reason

            # Process content
            if delta.content:
                content = delta.content
                accumulated_content.append(content)

                if first_token_time is None:
                    first_token_time = current_time

                # Calculate current usage
                completion_tokens = len(
                    self.tokenizer.encode("".join(accumulated_content))
                )
                # Need to fix this...
                current_usage = TokenUsage(
                    uncached_prompt_tokens=prompt_tokens,
                    cache_write_prompt_tokens=0,
                    cached_prompt_tokens=0,
                    completion_tokens=completion_tokens,
                )

                timing_info = TimingInfo(
                    start_time=start_time,
                    end_time=current_time,
                    total_duration=current_time - start_time,
                    first_token_time=first_token_time,
                    time_to_first_token=(
                        (first_token_time - start_time).total_seconds()
                        if first_token_time
                        else None
                    ),
                    tokens_per_second=(
                        completion_tokens / (current_time - start_time).total_seconds()
                        if first_token_time
                        else None
                    ),
                )

                chunk_completion = CompletionChunk(
                    id=chunk.id,
                    content=content,
                    model=model,
                    is_finished=chunk.choices[0].finish_reason is not None,
                    timing=timing_info,
                    usage=current_usage,
                    stop_reason=current_stop,
                )

                if chunk.choices[0].finish_reason is not None:
                    chunk_completion.raw_response = {
                        "finish_reason": chunk.choices[0].finish_reason,
                        "accumulated_content": "".join(accumulated_content),
                    }

                yield chunk_completion
