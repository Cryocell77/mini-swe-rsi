import logging
import os
import time
from typing import Any, Literal

from pydantic import BaseModel

from minisweagent.exceptions import FormatError
from minisweagent.models import GLOBAL_MODEL_STATS
from minisweagent.models.utils.actions_toolcall import (
    BASH_TOOL,
    format_toolcall_observation_messages,
    parse_toolcall_actions,
)
from minisweagent.models.utils.anthropic_utils import _reorder_anthropic_thinking_blocks
from minisweagent.models.utils.cache_control import set_cache_control
from minisweagent.models.utils.openai_multimodal import expand_multimodal_content
from minisweagent.models.utils.retry import retry

logger = logging.getLogger("portkey_model")

try:
    from portkey_ai import Portkey
except ImportError:
    raise ImportError(
        "The portkey-ai package is required to use PortkeyModel. Please install it with: pip install portkey-ai"
    )


class PortkeyModelConfig(BaseModel):
    model_name: str
    model_kwargs: dict[str, Any] = {}
    provider: str = ""
    """The LLM provider to use (e.g., 'openai', 'anthropic', 'google').
    If not specified, will be auto-detected from model_name.
    Required by Portkey when not using a virtual key.
    """
    set_cache_control: Literal["default_end"] | None = None
    """Set explicit cache control markers, for example for Anthropic models"""
    format_error_template: str = "{{ error }}"
    """Template used when the LM's output is not in the expected format."""
    observation_template: str = (
        "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
        "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"
    )
    """Template used to render the observation after executing an action."""
    multimodal_regex: str = ""
    """Regex to extract multimodal content. Empty string disables multimodal processing."""


class PortkeyModel:
    abort_exceptions: list[type[Exception]] = [KeyboardInterrupt, TypeError, ValueError]

    def __init__(self, *, config_class: type = PortkeyModelConfig, **kwargs):
        self.config = config_class(**kwargs)

        self._api_key = os.getenv("PORTKEY_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Portkey API key is required. Set it via the "
                "PORTKEY_API_KEY environment variable. You can permanently set it with "
                "`mini-extra config set PORTKEY_API_KEY YOUR_KEY`."
            )

        virtual_key = os.getenv("PORTKEY_VIRTUAL_KEY")
        client_kwargs = {"api_key": self._api_key}
        if virtual_key:
            client_kwargs["virtual_key"] = virtual_key
        elif self.config.provider:
            # If no virtual key but provider is specified, pass it
            client_kwargs["provider"] = self.config.provider

        self.client = Portkey(**client_kwargs)

    def _query(self, messages: list[dict[str, str]], **kwargs):
        return self.client.chat.completions.create(
            model=self.config.model_name,
            messages=messages,
            tools=[BASH_TOOL],
            **(self.config.model_kwargs | kwargs),
        )

    def _prepare_messages_for_api(self, messages: list[dict]) -> list[dict]:
        prepared = [{k: v for k, v in msg.items() if k != "extra"} for msg in messages]
        prepared = _reorder_anthropic_thinking_blocks(prepared)
        return set_cache_control(prepared, mode=self.config.set_cache_control)

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        for attempt in retry(logger=logger, abort_exceptions=self.abort_exceptions):
            with attempt:
                response = self._query(self._prepare_messages_for_api(messages), **kwargs)
        GLOBAL_MODEL_STATS.add()
        try:
            actions = self._parse_actions(response)
        except FormatError as e:
            try:
                e.messages[0]["extra"]["response"] = response.model_dump(mode="json")
            except Exception:
                # model_dump failed (e.g. unserializable object); fall back to repr
                # so the spec contract ("response MUST be persisted") holds unconditionally.
                e.messages[0]["extra"]["response"] = repr(response)
            raise
        message = response.choices[0].message.model_dump()
        message["extra"] = {
            "actions": actions,
            "response": response.model_dump(),
            "timestamp": time.time(),
        }
        return message

    def _parse_actions(self, response) -> list[dict]:
        """Parse tool calls from the response. Raises FormatError if unknown tool."""
        tool_calls = response.choices[0].message.tool_calls or []
        return parse_toolcall_actions(
            tool_calls,
            format_error_template=self.config.format_error_template,
            template_kwargs={"finish_reason": response.choices[0].finish_reason},
        )

    def format_message(self, **kwargs) -> dict:
        return expand_multimodal_content(kwargs, pattern=self.config.multimodal_regex)

    def format_observation_messages(
        self, message: dict, outputs: list[dict], template_vars: dict | None = None
    ) -> list[dict]:
        """Format execution outputs into tool result messages."""
        actions = message.get("extra", {}).get("actions", [])
        return format_toolcall_observation_messages(
            actions=actions,
            outputs=outputs,
            observation_template=self.config.observation_template,
            template_vars=template_vars,
            multimodal_regex=self.config.multimodal_regex,
        )

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return self.config.model_dump()

    def serialize(self) -> dict:
        return {
            "info": {
                "config": {
                    "model": self.config.model_dump(mode="json"),
                    "model_type": f"{self.__class__.__module__}.{self.__class__.__name__}",
                },
            }
        }

