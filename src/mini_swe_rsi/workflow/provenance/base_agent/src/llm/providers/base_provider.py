# Self-Improving Coding Agent
# Copyright (c) 2025 Maxime Robeyns
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""Base provider interface for LLM interactions."""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Union, List, AsyncGenerator, TypeVar, Any, Type
from datetime import datetime
from pydantic import BaseModel

from ..base import (
    Message,
    Completion,
    CompletionChunk,
    TimingInfo,
    Model,
)
from ...types.llm_types import TokenUsage, StopReason, ReasoningEffort, TextContent, ContentTypes
from ...types.tool_types import ToolInterface
from ...types.agent_types import AgentInterface


T = TypeVar("T")
logger = logging.getLogger(__name__)


class BaseProvider(ABC):
    """Abstract base class for LLM providers."""

    def map_stop_reason(self, response: Any) -> tuple[StopReason, Optional[str]]:
        """Map provider-specific stop information to standard format.

        Args:
            response: Raw API response

        Returns:
            Tuple of (stop_reason, stop_sequence)
        """
        # Default implementation assumes a normal completion
        return StopReason.COMPLETE, None

    def _prepare_continuation(
        self,
        messages: list[Message],
        prior_content: str,
    ) -> list[Message]:
        """Prepare messages for continuation after hitting token limit.

        Args:
            messages: Original message list
            prior_content: Content generated so far

        Returns:
            Updated messages ready for continuation
        """
        result = messages.copy()

        # Append or update assistant message with prior content
        if result and result[-1].role == "assistant":
            result[-1].content = [TextContent(text=prior_content)]
        else:
            result.append(Message(role="assistant", content=[TextContent(text=prior_content)]))

        return result

    def _get_timing_info(
        self,
        start_time: datetime,
        output_token_count: int,
        end_time: datetime | None = None,
        ftt: datetime | None = None,
    ):
        if end_time is None:
            end_time = datetime.now()
        total_duration = end_time - start_time
        return TimingInfo(
            start_time=start_time,
            end_time=end_time,
            total_duration=total_duration,
            first_token_time=ftt,
            time_to_first_token=((ftt - start_time).total_seconds() if ftt else None),
            tokens_per_second=(
                output_token_count / total_duration.total_seconds()
                if total_duration.total_seconds() > 0
                else None
            ),
        )

    async def create_continuation_completion(
        self,
        messages: List[Message],
        model: Model,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stop: Optional[Union[str, List[str]]] = None,
        top_p: float = 1.0,
        max_continuations: int = 4,
        reasoning_effort: ReasoningEffort | None = None,
        available_tools: list[Type[BaseModel]] | None = None,
    ) -> Completion:
        """Create a completion with automatic handling of max token limits.

        Note, we only accumulate the first text completion. Tool calls
        completions will cause us to break out of the loop.
        """
        start_time = datetime.now()
        first_token_time = None
        response = None
        accumulated_text_content = ""
        continuation_count = 0
        current_messages = messages.copy()

        if max_continuations <= 0:
            max_continuations = 1

        accumulated_usage = TokenUsage()

        while continuation_count < max_continuations:
            try:
                response = await self.create_completion(
                    messages=current_messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stop=stop,
                    top_p=top_p,
                    reasoning_effort=reasoning_effort,
                    available_tools=available_tools,
                )

                # Store first response for cache metrics
                if continuation_count == 0:
                    first_token_time = response.timing.first_token_time

                # Track token usage
                accumulated_usage += response.usage

                for block in response.content:  # there should only be one
                    if isinstance(block, TextContent):
                        accumulated_text_content += block.text

                # Accumulate content
                if not response.hit_token_limit:
                    #  We stopped for some reason other than running out of
                    #  output tokens; return here
                    break

                # Update messages for continuation
                current_messages = self._prepare_continuation(
                    messages=messages,
                    prior_content=accumulated_text_content,
                )

                continuation_count += 1

            except Exception as e:
                logger.warning(
                    f"Error during continuation attempt {continuation_count}: {e}"
                )
                timing_info = self._get_timing_info(
                    start_time,
                    output_token_count=accumulated_usage.completion_tokens,
                    ftt=first_token_time,
                )
                return Completion(
                    id=f"{model.id}-cont-error",
                    content=[TextContent(text=accumulated_text_content)],
                    model=model,
                    usage=accumulated_usage,
                    timing=timing_info,
                    cache_metrics=accumulated_usage.cache_metrics,
                    stop_reason=StopReason.ERROR,
                    raw_response={"error": str(e)},
                )

        # The last response may contain non-text content types
        response_content: list[ContentTypes] = [TextContent(text=accumulated_text_content)]
        if response is not None:
            for block in response.content:
                if not isinstance(block, TextContent):
                    response_content.append(block)
        # Get final stop information
        stop_reason = response.stop_reason if response is not None else StopReason.COMPLETE
        stop_sequence = response.stop_sequence if response is not None else None

        # Calculate timing information
        timing_info = self._get_timing_info(
            start_time,
            output_token_count=accumulated_usage.completion_tokens,
            ftt=first_token_time,
        )

        return Completion(
            id=f"{model}-cont-{continuation_count}",
            content=response_content,
            model=model,
            usage=accumulated_usage,
            timing=timing_info,
            cache_metrics=accumulated_usage.cache_metrics,
            stop_reason=stop_reason,
            stop_sequence=stop_sequence,
            continuation_count=continuation_count,
            raw_response=getattr(response, "raw_response", None),
        )

    async def create_streaming_continuation_completion(
        self,
        messages: List[Message],
        model: Model,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stop: Optional[Union[str, List[str]]] = None,
        top_p: float = 1.0,
        max_continuations: int = 4,
        reasoning_effort: ReasoningEffort | None = None,
        available_tools: list[Type[ToolInterface] | Type[AgentInterface]] | None = None,
    ) -> AsyncGenerator[CompletionChunk, None]:
        """Create a streaming completion with continuation support."""

        # TODO: fix this streaming method by making reference to the
        # non-streaming variant, which is more up-to-date.

        start_time = datetime.now()
        first_token_time = None
        total_input_tokens = 0
        total_output_tokens = 0
        continuation_count = 0
        current_content = []

        current_messages = messages.copy()

        while continuation_count < max_continuations:
            try:
                current_chunk_content = []
                chunk = None

                streaming_generator = None

                streaming_generator = self.create_streaming_completion(
                    messages=current_messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stop=stop,
                    top_p=top_p,
                    reasoning_effort=reasoning_effort,
                    available_tools=available_tools,
                )

                async for chunk in streaming_generator:
                    # Track first token time
                    if (
                        chunk.content
                        and first_token_time is None
                        and chunk.timing
                        and chunk.timing.first_token_time
                    ):
                        first_token_time = chunk.timing.first_token_time

                    # Track content
                    if chunk.content:
                        current_chunk_content.append(chunk.content)
                        yield chunk

                    # Update token counts on final chunk
                    if chunk.is_finished and chunk.usage:
                        total_input_tokens += chunk.usage.prompt_tokens
                        total_output_tokens += chunk.usage.completion_tokens

                # Store accumulated content
                chunk_content = "".join(current_chunk_content)
                if chunk_content:
                    current_content.append(chunk_content)

                # Check if we should continue
                if not chunk or not chunk.hit_token_limit:
                    break

                # Prepare for next continuation
                current_messages = self._prepare_continuation(
                    messages=messages,
                    prior_content="".join(current_content),
                )
                continuation_count += 1

            except Exception as e:
                logger.warning(
                    f"Error during streaming continuation {continuation_count}: {e}"
                )
                yield CompletionChunk(
                    id=f"{model.id}-cont-error",
                    content="",
                    model=model,
                    is_finished=True,
                    stop_reason=StopReason.ERROR,
                    raw_response={"error": str(e)},
                )
                break

        # Yield final stats
        end_time = datetime.now()
        total_duration = end_time - start_time

        final_timing = TimingInfo(
            start_time=start_time,
            end_time=end_time,
            total_duration=total_duration,
            first_token_time=first_token_time,
            time_to_first_token=(
                (first_token_time - start_time) if first_token_time else None
            ),
            tokens_per_second=(
                total_output_tokens / total_duration.total_seconds()
                if total_duration.total_seconds() > 0
                else None
            ),
        )

        # TODO: fix this
        final_usage = TokenUsage(
            uncached_prompt_tokens=total_input_tokens,
            completion_tokens=total_output_tokens,
        )

        # Get final stop information
        stop_reason, stop_sequence = (
            self.map_stop_reason(chunk) if chunk else (StopReason.ERROR, None)
        )

        yield CompletionChunk(
            id=f"{model.id}-cont-final",
            content="",
            model=model,
            is_finished=True,
            timing=final_timing,
            usage=final_usage,
            stop_reason=stop_reason,
            continuation_count=continuation_count,
            raw_response=getattr(chunk, "raw_response", None) if chunk else None,
        )

    # Abstract methods --------------------------------------------------------

    @abstractmethod
    def _prepare_messages(self, messages: list[Message]) -> Any:
        """Maps our framework-specific message list into provider-specific messages

        Note that this might involve agglomerating content blocks, or splitting
        out into multiple messages.
        """
        pass

    @abstractmethod
    async def create_completion(
        self,
        messages: List[Message],
        model: Model,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stop: Optional[Union[str, List[str]]] = None,
        top_p: float = 1.0,
        reasoning_effort: ReasoningEffort | None = None,
        available_tools: list[Type[BaseModel]] | None = None,
        num_completions: int = 1,
    ) -> Completion:
        """Create a completion using this provider."""
        pass

    @abstractmethod
    async def create_streaming_completion(
        self,
        messages: List[Message],
        model: Model,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stop: Optional[Union[str, List[str]]] = None,
        top_p: float = 1.0,
        reasoning_effort: ReasoningEffort | None = None,
        available_tools: list[dict] | None = None,
    ) -> AsyncGenerator[CompletionChunk, None]:
        """Create a streaming completion using this provider."""
        pass

    @abstractmethod
    def pydantic_to_native_tool(self, tool: ToolInterface | AgentInterface) -> dict:
        """
        Converts a BaseTool or BaseAgent into a schema for native tool calling
        for this particular provider.
        """
        pass
