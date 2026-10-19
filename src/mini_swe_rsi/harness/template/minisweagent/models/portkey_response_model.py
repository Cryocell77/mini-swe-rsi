import logging
import os
import time
from typing import Any

from pydantic import BaseModel

from minisweagent.exceptions import FormatError
from minisweagent.models import GLOBAL_MODEL_STATS
from minisweagent.models.utils.actions_toolcall_response import (
    BASH_TOOL_RESPONSE_API,
    finish_reason_from_responses_api,
    format_toolcall_observation_messages,
    parse_toolcall_actions_response,
)
from minisweagent.models.utils.retry import retry

logger = logging.getLogger("portkey_response_model")

try:
    from portkey_ai import Portkey
except ImportError:
    raise ImportError(
        "The portkey-ai package is required to use PortkeyResponseAPIModel. Please install it with: pip install portkey-ai"
    )


class PortkeyResponseAPIModelConfig(BaseModel):
    model_name: str
    model_kwargs: dict[str, Any] = {}
    format_error_template: str = "{{ error }}"
    observation_template: str = (
        "{% if output.exception_info %}<exception>{{output.exception_info}}</exception>\n{% endif %}"
        "<returncode>{{output.returncode}}</returncode>\n<output>\n{{output.output}}</output>"
    )
    multimodal_regex: str = ""


class PortkeyResponseAPIModel:
    """Portkey model using the Responses API with native tool calling.

    Note: This implementation is stateless - each request must include
    the full conversation history. previous_response_id is not used.
    """

    abort_exceptions: list[type[Exception]] = [KeyboardInterrupt, TypeError, ValueError]

    def __init__(self, **kwargs):
        self.config = PortkeyResponseAPIModelConfig(**kwargs)

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

        self.client = Portkey(**client_kwargs)

    def _query(self, messages: list[dict[str, str]], **kwargs):
        return self.client.responses.create(
            model=self.config.model_name,
            input=messages,
            tools=[BASH_TOOL_RESPONSE_API],
            **(self.config.model_kwargs | kwargs),
        )

    def _prepare_messages_for_api(self, messages: list[dict]) -> list[dict]:
        """Prepare messages for Portkey's stateless Responses API.

        Flattens response objects into their output items.
        """
        result = []
        for msg in messages:
            if msg.get("object") == "response":
                for item in msg.get("output", []):
                    result.append({k: v for k, v in item.items() if k != "extra"})
            else:
                result.append({k: v for k, v in msg.items() if k != "extra"})
        return result

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        for attempt in retry(logger=logger, abort_exceptions=self.abort_exceptions):
            with attempt:
                response = self._query(self._prepare_messages_for_api(messages), **kwargs)
        GLOBAL_MODEL_STATS.add()
        try:
            actions = self._parse_actions(response)
        except FormatError as e:
            # hasattr guard: Portkey returns a pydantic object, but tests may inject a plain dict.
            # Inner try: if serialization itself fails, repr() guarantees the key is always set.
            try:
                e.messages[0]["extra"]["response"] = (
                    response.model_dump(mode="json") if hasattr(response, "model_dump") else dict(response)
                )
            except Exception:
                e.messages[0]["extra"]["response"] = repr(response)
            raise
        message = response.model_dump() if hasattr(response, "model_dump") else dict(response)
        message["extra"] = {
            "actions": actions,
            "timestamp": time.time(),
        }
        return message

    def _parse_actions(self, response) -> list[dict]:
        """Parse tool calls from the response API response."""
        output = response.output if hasattr(response, "output") else response.get("output", [])
        return parse_toolcall_actions_response(
            output,
            format_error_template=self.config.format_error_template,
            template_kwargs={"finish_reason": finish_reason_from_responses_api(response)},
        )


    def format_message(self, **kwargs) -> dict:
        role = kwargs.get("role", "user")
        content = kwargs.get("content", "")
        extra = kwargs.get("extra")
        content_items = [{"type": "input_text", "text": content}] if isinstance(content, str) else content
        msg = {"type": "message", "role": role, "content": content_items}
        if extra:
            msg["extra"] = extra
        return msg

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

    def get_template_vars(self, **kwargs) -> dict:
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
