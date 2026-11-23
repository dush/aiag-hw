"""MCP servers used by the agent, loaded with `langchain.mcp.MCPAdapter`.

- ares: `ares-gov-cz-mcp` (https://github.com/iquest/ares-mcp) run with uvx, always on.
  Its raw results are compacted by `ares_tools.compact_ares_tools()`.
- wikipedia: `wikipedia-mcp` run with uvx, Czech Wikipedia by default. Off with WIKIPEDIA_MCP=0.
- tavily: `tavily-mcp` run with npx, only when TAVILY_API_KEY is set.

Each server gets its own adapter, so a server that fails to start (no network,
no npx) is skipped and the others keep working. With one server per adapter the
tool names keep their original form (no `{server}_` prefix).

Only the tools in ALLOWED_TOOLS reach the model: wikipedia-mcp alone exposes about
20 tools, which is too many for a small free model.
"""

import logging
import os
import warnings
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastmcp.client.transports import StdioTransport
from langchain_core._api import LangChainBetaWarning
from langchain_core.tools import BaseTool

from company_checker.ares_tools import compact_ares_tools

with warnings.catch_warnings():
    warnings.simplefilter("ignore", LangChainBetaWarning)  # langchain.mcp is beta
    from langchain.mcp import MCPAdapter

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"

ALLOWED_TOOLS = {
    "ares": {"search_entities", "get_entity", "get_register_record"},
    "wikipedia": {"search_wikipedia", "get_summary"},
    "tavily": {"tavily-search", "tavily_search"},
}

ARES_PACKAGE = "ares-gov-cz-mcp==0.1.0"

# Variables a stdio server inherits; everything else (API keys ...) is passed explicitly.
INHERITED_ENV = ("HOME", "LOGNAME", "PATH", "SHELL", "USER", "LANG")


def _base_env() -> dict[str, str]:
    return {key: os.environ[key] for key in INHERITED_ENV if key in os.environ}


def server_transports() -> dict[str, StdioTransport]:
    """Stdio transports of the enabled servers. Server stderr goes to logs/<name>.log."""
    LOG_DIR.mkdir(exist_ok=True)
    env = _base_env()

    def stdio(name: str, command: str, args: list[str], **kwargs) -> StdioTransport:
        return StdioTransport(command, args, env=kwargs.pop("env", env),
                              log_file=LOG_DIR / f"{name}.log", **kwargs)

    servers = {
        "ares": stdio("ares", "uvx", [ARES_PACKAGE]),
    }
    if os.getenv("WIKIPEDIA_MCP", "1") != "0":
        servers["wikipedia"] = stdio(
            "wikipedia", "uvx", ["wikipedia-mcp", "--language", os.getenv("WIKIPEDIA_LANGUAGE", "cs")]
        )
    if key := os.getenv("TAVILY_API_KEY"):
        servers["tavily"] = stdio("tavily", "npx", ["-y", "tavily-mcp@latest"],
                                  env={**env, "TAVILY_API_KEY": key})
    return servers


@asynccontextmanager
async def open_tools() -> AsyncIterator[list[BaseTool]]:
    """Yield the allowed tools with every server's session held open for the whole run.

    The tools from `MCPAdapter.list_tools()` open the client on each call; keeping the
    adapter context open makes them reuse one session instead of reconnecting.
    """
    transports = server_transports()
    tools: list[BaseTool] = []
    async with AsyncExitStack() as stack:
        for name, transport in transports.items():
            # Stdio transports keep the subprocess alive between sessions; stop it at the end.
            stack.push_async_callback(transport.close)
            try:
                adapter = await stack.enter_async_context(MCPAdapter(transport))
                server_tools = await adapter.list_tools()
            except Exception:
                if name == "ares":
                    raise
                logger.warning("MCP server %s is not available (see logs/%s.log); "
                               "continuing without it", name, name, exc_info=True)
                continue
            allowed = [t for t in server_tools if t.name in ALLOWED_TOOLS[name]]
            if not allowed:
                logger.warning("MCP %s: none of its tools %s is in ALLOWED_TOOLS",
                               name, [t.name for t in server_tools])
            if name == "ares":
                allowed = compact_ares_tools(allowed)
            logger.info("MCP %s: %s", name, [t.name for t in allowed])
            tools.extend(allowed)
        yield tools
