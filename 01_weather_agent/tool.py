import inspect
from collections.abc import Callable
from typing import Any

from agents.function_schema import FuncSchema, function_schema


class Tool:
    """Wraps a function or callable object and exposes its JSON schema.

    tool = Tool(WeatherForecast(days=5))
    tool.schema      # OpenAI function-tool definition
    tool(**args)     # calls the wrapped callable
    """

    def __init__(
        self,
        func: Callable[..., Any],
        name: str | None = None,
        description: str | None = None,
    ):
        self.func = func
        if inspect.isfunction(func) or inspect.ismethod(func):
            schema_source = func
            name = name or func.__name__
        else:
            # Callable object: introspect the bound __call__ (no `self` parameter)
            # and take the name and description from its class.
            schema_source = func.__call__
            name = name or type(func).__name__
            description = description or inspect.getdoc(type(func))
        self.func_schema: FuncSchema = function_schema(
            schema_source,
            name_override=name,
            description_override=description,
        )

    @property
    def name(self) -> str:
        return self.func_schema.name

    @property
    def description(self) -> str | None:
        return self.func_schema.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self.func_schema.params_json_schema

    @property
    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
                "strict": self.func_schema.strict_json_schema,
            },
        }

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)
