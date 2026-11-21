"""Regression test of the RUIAN Search tools against the real `ruian` database (no LLM involved).

Postgres is not published to the host, so the checks run inside the `langflow` container. The
component is loaded from its code string, the same way Langflow runs the node in a flow. Expected
values come from data/20261031_OB_ADR_csv.zip (see tests/ruian_agent_eval.json for the SQL).
Exits non-zero when a check fails.

Usage (from the host, with the stack running): uv run python scripts/smoke_ruian_search.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "components" / "ruian_search_component.py"

CHECKS = r'''
import asyncio
import json
import sys
import time

from lfx.custom.eval import eval_custom_component_code

failures = []
# One of the 904 address places without coordinates (SELECT kod FROM adresni_misto WHERE poloha IS NULL).
NO_COORDINATES_ID = 330400


def check(label, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {label}{'' if ok else ' -- ' + str(detail)[:300]}")
    if not ok:
        failures.append(label)


async def call(tool, **args):
    start = time.perf_counter()
    out = await TOOLS[tool].ainvoke(args)
    ms = (time.perf_counter() - start) * 1000
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        print(f"     {tool}({args}) -> error ({ms:.0f} ms): {out}")
        return out
    print(f"     {tool}({args}) -> {len(data['items'])} items, total={data.get('total', '-')} ({ms:.0f} ms)")
    return data


async def main():
    global TOOLS
    TOOLS = {t.name: t for t in await eval_custom_component_code(CODE)().to_toolkit()}
    check("nine tools", sorted(TOOLS) == sorted([
        "search_municipality", "search_prague_district", "search_city_district", "search_municipality_part",
        "search_street", "search_address_place", "search_address", "search_nearby", "address_distances"]), list(TOOLS))

    r = await call("search_municipality", name_match="praha")
    check("Praha id", r["items"][0]["id"] == 554782, r)
    r = await call("search_municipality", name_match="Lhota", name_mode="exact")
    check("4 municipalities named exactly Lhota", r["total"] == 4 and all(i["name"] == "Lhota" for i in r["items"]), r)

    r = await call("search_street", name_match="nadraz", municipality_id_in=[582786])
    check("word_prefix: 2 streets in Brno", r["total"] == 2, r)
    r = await call("search_street", name_match="Nádraž", name_mode="starts_with", municipality_id_in=[582786])
    check("starts_with: only Nádražní", [i["name"] for i in r["items"]] == ["Nádražní"], r)
    r = await call("search_street", name_match="null", municipality_id_in=[554782], limit=1)
    check("name_match 'null' is ignored", r["total"] == 7680, r)

    plzen = (await call("search_municipality", name_match="Plzeň", name_mode="exact"))["items"][0]["id"]
    r = await call("search_street", municipality_id_in=plzen, limit=5)
    check("Plzeň has 1206 streets (total, single id accepted)", r["total"] == 1206 and r["has_more"], r)
    r = await call("search_city_district", municipality_id_in=[582786])
    check("Brno has 29 city districts", r["total"] == 29, r)

    r = await call("search_address_place", street_id_in=[478156], orientation_number=36)
    item = r["items"][0]
    check("Vodičkova 36 formatted", item["formatted"] == "Vodičkova 704/36, Nové Město, 110 00 Praha 1", item)
    check("Vodičkova 36 GPS", abs(item["lat"] - 50.081412) < 5e-5 and abs(item["lon"] - 14.425295) < 5e-5, item)
    check("compact item", "street_name" not in item and "sjtsk_x" not in item, item)
    r = await call("search_address_place", street_id_in=[478156], orientation_number=36, detail=True)
    check("detail item", {"street_name", "sjtsk_x", "valid_from"} <= set(r["items"][0]), r["items"][0])
    r = await call("search_address_place", postal_code_in=["110 00"], limit=2)
    check("2860 addresses with PSČ 110 00", r["total"] == 2860 and not r["total_is_lower_bound"], r)
    r = await call("search_address_place", municipality_id_in=[554782], limit=1)
    check("Praha addresses: capped total", r["total"] == 10000 and r["total_is_lower_bound"], r)

    r = await call("search_address", query="Vodičkova 36, Praha")
    check("search_address Vodičkova 36 Praha", r["items"][0]["id"] == 21704970, r)
    r = await call("search_address", query="Bukovina č. p. 5, PSČ 679 05")
    check("search_address village without street", r["items"][0]["formatted"].startswith("Bukovina 5,"), r)
    r = await call("search_address", query="náměstí Svobody 1 Brno")
    check("search_address náměstí Svobody 1 Brno", r["items"][0]["postal_code"] == "60200", r)
    r = await call("search_address", query="Vodičkova 999 Praha")
    check("search_address nonexistent", r["total"] == 0 and r["items"] == [], r)

    # Distances (S-JTSK metres; ground truth from SQL on souradnice_y/x).
    r = await call("search_nearby", address_id=21704970, radius_m=50)
    check("6 addresses within 50 m of Vodičkova 36", r["total"] == 6 and r["origin"] == {"address_id": 21704970}, r)
    first = r["items"][0]
    check("nearest is Vodičkova 37 at 22 m", first["id"] == 21704988 and first["distance_m"] == 22, first)
    check("origin not listed", all(i["id"] != 21704970 for i in r["items"]), r)
    check("nearest first", [i["distance_m"] for i in r["items"]] == sorted(i["distance_m"] for i in r["items"]), r)
    r = await call("search_nearby", lat=50.081412, lon=14.425295, radius_m=50)
    check("by lat/lon: Vodičkova 36 itself at ~0 m", r["items"][0]["id"] == 21704970 and r["items"][0]["distance_m"] <= 2, r)
    r = await call("search_nearby", address_id=21704970, radius_m=100, street_id_in=478156)
    check("street filter", r["items"] and all(i["street_id"] == 478156 for i in r["items"]), r)
    r = await call("address_distances", from_address_id=21704970, to_address_ids=[21728208, 19094388, 999999999, 21728208])
    got = {i["id"]: i["distance_m"] for i in r["items"]}
    check("Vodičkova 36 -> Karlovo nám. 13: 730 m", got.get(21728208) == 730, r)
    check("Vodičkova 36 -> nám. Svobody 1 Brno: 185850 m", got.get(19094388) == 185850, r)
    check("unknown id in not_found, duplicates dropped", r["not_found"] == [999999999] and len(r["items"]) == 2, r)
    r = await call("search_address_place", id_in=[NO_COORDINATES_ID], detail=True)
    check("test address has no coordinates", r["items"] and "lat" not in r["items"][0], r)
    r = await call("search_nearby", address_id=NO_COORDINATES_ID)
    check("error: origin without coordinates", "has no coordinates" in r, r)
    r = await call("address_distances", from_address_id=21704970, to_address_ids=[NO_COORDINATES_ID, 21728208])
    check("target without coordinates", r["without_coordinates"] == [NO_COORDINATES_ID]
          and [i["id"] for i in r["items"]] == [21728208], r)
    r = await call("search_nearby", lat=14.425295, lon=50.081412)
    check("error: swapped lat/lon", "look swapped" in r, r)
    r = await call("search_nearby", address_id=999999999)
    check("error: unknown origin", "does not exist" in r, r)
    r = await call("search_nearby", address_id=21704970, radius_m=50000)
    check("error: radius", r.startswith("Invalid arguments: radius_m"), r)

    r = await call("search_street", sort=["relevance"])
    check("error: relevance without name_match", r == "Sort by relevance needs name_match.", r)
    r = await call("search_address_place")
    check("error: address place without filter", "at least one filter" in r, r)
    r = await call("search_municipality", limit=500)
    check("error: limit", r.startswith("Invalid arguments: limit"), r)
    r = await call("search_address", query="č. p.")
    check("error: empty address query", r.startswith("query must contain"), r)

    print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
    sys.exit(1 if failures else 0)


asyncio.run(main())
'''


def main() -> int:
    program = f"CODE = {COMPONENT.read_text(encoding='utf-8')!r}\n{CHECKS}"
    cmd = ["docker", "compose", "exec", "-T", "langflow", "python", "-"]
    return subprocess.run(cmd, input=program, text=True, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
