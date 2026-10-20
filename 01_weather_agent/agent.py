import dataclasses
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from tool import Tool

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "openrouter-gemma"  # free OpenRouter model, aliased in litellm_config.yaml
DEFAULT_BASE_URL = "http://0.0.0.0:4000"  # local LiteLLM proxy
DEFAULT_API_KEY = "dummy-key"
MAX_TOOL_ROUNDS = 10
LOG_PREVIEW_CHARS = 200


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    result: str


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class AgentResponse:
    content: str | None
    model: str
    finish_reason: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    messages: list[dict[str, Any]] = field(default_factory=list)


def _to_json(value: Any) -> str:
    def default(obj: Any) -> Any:
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
        return str(obj)

    return json.dumps(value, default=default, ensure_ascii=False)


def _preview(text: str) -> str:
    if len(text) <= LOG_PREVIEW_CHARS:
        return text
    return f"{text[:LOG_PREVIEW_CHARS]}... ({len(text)} chars)"


class Agent:
    """Chat agent that calls tools until the model produces a final answer.

    agent = Agent(instructions="You are a weather assistant.", tools=[Tool(Geocoder())])
    response = agent.ask("Weather in Prague?")
    response.content, response.tool_calls, response.usage

    Defaults: local LiteLLM proxy (LITELLM_BASE_URL / LITELLM_API_KEY) and a free
    OpenRouter model. Each ask() is independent; no history is kept between calls.

    Logs to the "agent" logger (this module); configure handlers in the application,
    e.g. logging.basicConfig(level=logging.INFO). DEBUG adds tool arguments and results.
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        base_url: str | None = None,
        instructions: str | None = None,
        tools: list[Tool] | None = None,
    ):
        self.model = model
        self.instructions = instructions
        self.tools = {tool.name: tool for tool in tools or []}
        base_url = base_url or os.getenv("LITELLM_BASE_URL", DEFAULT_BASE_URL)
        self.client = OpenAI(
            api_key=api_key or os.getenv("LITELLM_API_KEY", DEFAULT_API_KEY),
            base_url=base_url,
        )
        logger.info(
            "Agent ready: model=%s base_url=%s tools=%s",
            model, base_url, list(self.tools) or "none",
        )

    def ask(self, prompt: str) -> AgentResponse:
        messages: list[dict[str, Any]] = []
        if self.instructions:
            messages.append({"role": "developer", "content": self.instructions})
        messages.append({"role": "user", "content": prompt})

        logger.info("ask: %s", _preview(prompt))
        tool_calls: list[ToolCall] = []
        usage = Usage()
        kwargs: dict[str, Any] = {}
        if self.tools:
            kwargs["tools"] = [tool.schema for tool in self.tools.values()]

        for round_number in range(1, MAX_TOOL_ROUNDS + 1):
            logger.debug("round %d: sending %d messages", round_number, len(messages))
            try:
                completion = self.client.chat.completions.create(
                    model=self.model, messages=messages, **kwargs
                )
            except Exception:
                logger.exception("round %d: model request failed", round_number)
                raise
            if completion.usage:
                usage.prompt_tokens += completion.usage.prompt_tokens
                usage.completion_tokens += completion.usage.completion_tokens
                usage.total_tokens += completion.usage.total_tokens

            choice = completion.choices[0]
            message = choice.message
            # Rebuild by hand: proxies add extra fields that the next request may reject.
            assistant: dict[str, Any] = {"role": "assistant", "content": message.content}
            if message.tool_calls:
                assistant["tool_calls"] = [
                    call.model_dump(include={"id", "type", "function"})
                    for call in message.tool_calls
                ]
            messages.append(assistant)

            if not message.tool_calls:
                if not message.content:
                    logger.warning(
                        "Empty answer from %s (finish_reason=%s)",
                        completion.model, choice.finish_reason,
                    )
                elif choice.finish_reason == "length":
                    logger.warning("Answer truncated (finish_reason=length)")
                logger.info(
                    "answer: model=%s finish_reason=%s tool_calls=%d tokens=%d",
                    completion.model, choice.finish_reason, len(tool_calls), usage.total_tokens,
                )
                return AgentResponse(
                    content=message.content,
                    model=completion.model,
                    finish_reason=choice.finish_reason,
                    tool_calls=tool_calls,
                    usage=usage,
                    messages=messages,
                )

            for call in message.tool_calls:
                record = self._run_tool(call.id, call.function.name, call.function.arguments)
                tool_calls.append(record)
                messages.append(
                    {"role": "tool", "tool_call_id": record.id, "content": record.result}
                )

        logger.error("No final answer after %d tool rounds", MAX_TOOL_ROUNDS)
        raise RuntimeError(f"No final answer after {MAX_TOOL_ROUNDS} tool rounds")

    def _run_tool(self, call_id: str, name: str, raw_arguments: str) -> ToolCall:
        arguments: dict[str, Any] = {}
        logger.info("tool call: %s", name)
        try:
            tool = self.tools[name]
            arguments = json.loads(raw_arguments or "{}")
            logger.debug("tool %s arguments: %s", name, arguments)
            result = _to_json(tool(**arguments))
            logger.debug("tool %s result: %s", name, _preview(result))
        except Exception as exc:  # report to the model so it can recover
            result = f"Error: {type(exc).__name__}: {exc}"
            logger.warning("tool %s failed: %s", name, result)
        return ToolCall(id=call_id, name=name, arguments=arguments, result=result)
