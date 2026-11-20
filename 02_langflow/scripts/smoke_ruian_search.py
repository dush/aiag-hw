"""Smoke test for the RUIAN Search component: build its tools and run a search chain.

Postgres is not published to the host, so the test runs inside the `langflow` container. The
component is loaded from its code string, the same way Langflow runs the node in a flow.

Usage (from the host, with the stack running): uv run python scripts/smoke_ruian_search.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "components" / "ruian_search_component.py"

CHAIN = '''
import asyncio
import json
import time

from lfx.custom.eval import eval_custom_component_code


async def call(tools, tool, **args):
    start = time.perf_counter()
    out = await tools[tool].ainvoke(args)
    ms = (time.perf_counter() - start) * 1000
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        print(f"{tool}({args}) -> ERROR ({ms:.0f} ms): {out}")
        return {}
    print(f"{tool}({args}) -> {len(data['items'])} items, has_more={data['has_more']} ({ms:.0f} ms)")
    for item in data["items"][:5]:
        print("   ", item.get("formatted") or {k: item[k] for k in list(item)[:4]})
    return data


async def main():
    component = eval_custom_component_code(CODE)()
    tools = {t.name: t for t in await component.to_toolkit()}
    print("tools:", ", ".join(tools))

    await call(tools, "search_municipality", name_match="praha")
    brno = await call(tools, "search_municipality", name_match="brno", limit=3)
    await call(tools, "search_street", name_match="nadraz", municipality_id_in=[brno["items"][0]["id"]])
    await call(tools, "search_city_district", name_match="brno str")
    vod = await call(tools, "search_street", name_match="vodickova", municipality_id_in=[554782])
    await call(tools, "search_address_place", street_id_in=[vod["items"][0]["id"]], orientation_number=36)
    await call(tools, "search_address_place", municipality_id_in=[554782], limit=3)
    await call(tools, "search_address_place", postal_code_in=["110 00"], limit=2)
    await call(tools, "search_municipality", id_in=[582786, 554782], sort=["-name"])
    await call(tools, "search_street", sort=["relevance"])  # expected error
    await call(tools, "search_address_place")  # expected error
    await call(tools, "search_municipality", limit=500)  # expected error


asyncio.run(main())
'''


def main() -> int:
    program = f"CODE = {COMPONENT.read_text(encoding='utf-8')!r}\n{CHAIN}"
    cmd = ["docker", "compose", "exec", "-T", "langflow", "python", "-"]
    return subprocess.run(cmd, input=program, text=True, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
