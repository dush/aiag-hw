"""Compaction of ARES MCP results. Fake tools stand in for the server; fixtures are synthetic."""

import json

import pytest
from langchain_core.tools import StructuredTool, ToolException

from company_checker.ares_tools import compact_tool

ICO = "99999994"  # valid check digit, not in ARES (checked 2026-11-21)

PERSON = {
    "jmeno": "JANA",
    "prijmeni": "VZOROVÁ",
    "datumNarozeni": "1980-01-01",
    "adresa": {"textovaAdresa": "Smyšlená 1, 99999 Nikde"},
}

SUBJECT = {
    "ico": ICO,
    "obchodniJmeno": "Vzorová firma s.r.o.",
    "pravniForma": "112",
    "sidlo": {"textovaAdresa": "Smyšlená 2, 99999 Nikde"},
    "datumVzniku": "2010-05-01",
    "dic": f"CZ{ICO}",
    "czNace2008": ["62010"],
    "seznamRegistraci": {"stavZdrojeVr": "AKTIVNI", "stavZdrojeDph": "AKTIVNI",
                         "stavZdrojeIr": "NEEXISTUJICI"},
}

REGISTER = {
    "icoId": ICO,
    "zaznamy": [{
        "primarniZaznam": True,
        "rejstrik": "OR",
        "stavSubjektu": "AKTIVNI",
        "datumZapisu": "2010-05-01",
        "spisovaZnacka": [{"soud": "MSPH", "oddil": "C", "vlozka": 1}],
        "obchodniJmeno": [
            {"hodnota": "Vzorová firma s.r.o.", "datumZapisu": "2015-01-01"},
            {"hodnota": "Stará firma s.r.o.", "datumZapisu": "2010-05-01", "datumVymazu": "2015-01-01"},
        ],
        "zakladniKapital": [{"vklad": {"typObnos": "KORUNY", "hodnota": "200000;00"}}],
        "statutarniOrgany": [{
            "nazevOrganu": "Statutární orgán",
            "clenoveOrganu": [
                {"datumZapisu": "2010-05-01", "nazevAngazma": "Jednatel",
                 "clenstvi": {"funkce": {"nazev": "jednatel"}}, "fyzickaOsoba": PERSON},
                {"datumZapisu": "2010-05-01", "datumVymazu": "2012-01-01",
                 "nazevAngazma": "Jednatel", "fyzickaOsoba": {"jmeno": "BÝVALÝ", "prijmeni": "JEDNATEL"}},
            ],
        }],
        "spolecnici": [{
            "nazevOrganu": "Společníci",
            "spolecnik": [{
                "osoba": {"pravnickaOsoba": {"obchodniJmeno": "Matka a.s.", "ico": "27082440"}},
                "podil": [{"vklad": {"typObnos": "KORUNY", "hodnota": "200000;00"},
                           "velikostPodilu": {"typObnos": "TEXT", "hodnota": "100 %"}}],
            }],
        }],
    }],
}


def fake_mcp_tool(name: str, result: dict | None = None, error: str | None = None,
                  seen: list | None = None) -> StructuredTool:
    """Like an MCPAdapter tool: content and artifact with structured_content, errors as ToolException."""

    async def run(**kwargs):
        if seen is not None:
            seen.append(kwargs)
        if error:
            raise ToolException(error)
        return json.dumps(result), {"structured_content": result}

    return StructuredTool.from_function(
        coroutine=run, name=name, description=f"{name} description",
        args_schema={"type": "object", "properties": {"ico": {"type": "string"},
                                                      "register": {"type": "string"}}},
        response_format="content_and_artifact", handle_tool_error=True,
    )


async def call(tool, args: dict) -> str:
    message = await tool.ainvoke({"name": tool.name, "args": args, "id": "t1", "type": "tool_call"})
    return message.content


async def test_entity_summary():
    tool = compact_tool(fake_mcp_tool("get_entity", SUBJECT))
    assert tool.name == "get_entity" and tool.description == "get_entity description"

    result = json.loads(await call(tool, {"ico": ICO}))

    assert result["nazev"] == "Vzorová firma s.r.o."
    assert result["pravni_forma"] == "společnost s ručením omezeným"
    assert result["zanikla"] is False
    assert result["v_insolvencnim_rejstriku"] is False
    assert "plátce DPH" in result["aktivni_registrace"]


async def test_register_keeps_current_and_drops_personal_data():
    seen: list = []
    tool = compact_tool(fake_mcp_tool("get_register_record", REGISTER, seen=seen))

    text = await call(tool, {"register": "vr", "ico": ICO})
    result = json.loads(text)

    assert seen == [{"register": "vr", "ico": ICO}]
    assert result["spisova_znacka"] == ["C 1 vedená u MSPH"]
    assert result["zakladni_kapital"] == "200000,00 Kč"
    assert result["predchozi_nazvy"] == ["Stará firma s.r.o."]
    members = result["statutarni_organy"][0]["clenove"]
    assert members == [{"jmeno": "JANA VZOROVÁ", "funkce": "jednatel", "od": "2010-05-01"}]
    assert result["spolecnici_a_akcionari"] == [
        {"jmeno": "Matka a.s. (IČO 27082440)", "vklad": "200000,00 Kč", "podil": "100 %"}
    ]
    assert "1980-01-01" not in text
    assert "Smyšlená 1" not in text
    assert "BÝVALÝ" not in text


async def test_other_registers_are_refused():
    seen: list = []
    tool = compact_tool(fake_mcp_tool("get_register_record", REGISTER, seen=seen))

    text = await call(tool, {"register": "rzp", "ico": ICO})

    assert "Only register 'vr'" in text
    assert seen == []  # the server was not called


async def test_search_summary():
    page = {"total": 1, "start": 0, "count": 1, "items": [SUBJECT], "next_start": None}
    tool = compact_tool(fake_mcp_tool("search_entities", page))

    result = json.loads(await call(tool, {"ico": ICO}))

    assert result == {"pocet_celkem": 1, "subjekty": [{
        "ico": ICO, "nazev": "Vzorová firma s.r.o.", "sidlo": "Smyšlená 2, 99999 Nikde",
        "datum_zaniku": None}]}


async def test_server_error_reaches_the_model():
    tool = compact_tool(fake_mcp_tool("get_entity", error="[not_found] ARES has no record"))

    message = await tool.ainvoke({"name": "get_entity", "args": {"ico": ICO}, "id": "t1",
                                  "type": "tool_call"})

    assert message.status == "error"
    assert "[not_found]" in message.content


def test_unknown_tool_is_rejected():
    with pytest.raises(KeyError):
        compact_tool(fake_mcp_tool("search_addresses", {}))
