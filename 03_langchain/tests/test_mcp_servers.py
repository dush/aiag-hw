"""open_tools() with the real ARES server (ares-gov-cz-mcp via uvx; listing needs no ARES call)."""

import asyncio

from fastmcp.client.transports import StdioTransport

from company_checker import mcp_servers

ARES_TOOLS = ["get_entity", "get_register_record", "search_entities"]


async def test_failing_optional_server_is_skipped(monkeypatch, tmp_path):
    real = mcp_servers.server_transports

    def transports():
        servers = {"ares": real()["ares"]}
        servers["wikipedia"] = StdioTransport("command-that-does-not-exist-xyz", [],
                                              log_file=tmp_path / "wikipedia.log")
        return servers

    monkeypatch.setattr(mcp_servers, "server_transports", transports)
    monkeypatch.setenv("WIKIPEDIA_MCP", "0")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    async with asyncio.timeout(60):
        async with mcp_servers.open_tools() as tools:
            assert sorted(t.name for t in tools) == ARES_TOOLS
