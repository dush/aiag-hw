"""Start the MCP servers, list their tools and call the ARES tools once.

uv run python -m scripts.smoke_mcp [IČO]
"""

import asyncio
import sys

from dotenv import load_dotenv

from company_checker.mcp_servers import open_tools


async def main(ico: str) -> None:
    async with open_tools() as tools:
        by_name = {tool.name: tool for tool in tools}
        print("tools:", sorted(by_name))
        calls = [
            ("get_entity", {"ico": ico}),
            ("get_register_record", {"register": "vr", "ico": ico}),
            ("search_entities", {"filter": {"obchodniJmeno": "Kofola ČeskoSlovensko"}, "count": 3}),
        ]
        for name, args in calls:
            text = str(await by_name[name].ainvoke(args))
            print(f"\n{name}: {len(text)} chars\n{text[:1200]}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "27082440"))
