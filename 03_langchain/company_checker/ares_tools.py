"""Compact the results of the ARES MCP server before the model sees them.

The ARES tools come from `ares-gov-cz-mcp` (https://github.com/iquest/ares-mcp), which
returns raw ARES JSON. A commercial register record holds the full history (about 62 KB
for Alza) and the birth dates and home addresses of board members and shareholders.
`compact_ares_tools()` wraps the allowed tools so that the model gets only the current
state with names and roles of natural persons, never their birth dates or addresses.
"""

import json
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool, ToolException

# Most common legal forms (ARES code list "PravniForma").
LEGAL_FORMS = {
    "100": "podnikající fyzická osoba",
    "101": "fyzická osoba podnikající dle živnostenského zákona",
    "111": "veřejná obchodní společnost",
    "112": "společnost s ručením omezeným",
    "113": "společnost komanditní",
    "121": "akciová společnost",
    "141": "obecně prospěšná společnost",
    "205": "družstvo",
    "301": "státní podnik",
    "325": "organizační složka státu",
    "331": "příspěvková organizace",
    "706": "spolek",
    "801": "obec",
}

# Registers listed in seznamRegistraci (prefix "stavZdroje" stripped).
REGISTERS = {
    "Vr": "veřejný (obchodní) rejstřík",
    "Res": "registr ekonomických subjektů",
    "Rzp": "živnostenský rejstřík",
    "Dph": "plátce DPH",
    "Ir": "insolvenční rejstřík",
    "Ceu": "centrální evidence úpadců",
    "Rs": "rejstřík škol",
    "Nrpzs": "registr poskytovatelů zdravotních služeb",
}


def _current(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Records of the commercial register that are still valid (not deleted)."""
    return [item for item in items or [] if not item.get("datumVymazu")]


def _person_name(person: dict[str, Any]) -> str:
    """Name of a natural or legal person; drops birth date and address."""
    if natural := person.get("fyzickaOsoba"):
        parts = [natural.get("titulPredJmenem"), natural.get("jmeno"), natural.get("prijmeni"),
                 natural.get("titulZaJmenem")]
        return " ".join(p for p in parts if p).strip() or "(neuvedeno)"
    if legal := person.get("pravnickaOsoba"):
        name = legal.get("obchodniJmeno") or "(neuvedeno)"
        return f"{name} (IČO {legal['ico']})" if legal.get("ico") else name
    return "(neuvedeno)"


def _amount(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    amount = str(value.get("hodnota", "")).replace(";", ",")
    unit = {"KORUNY": "Kč", "PROCENTA": "%", "TEXT": ""}.get(
        value.get("typObnos", ""), value.get("typObnos", "")
    )
    return f"{amount} {unit}".strip()


def summarize_subject(data: dict[str, Any]) -> dict[str, Any]:
    """Basic data from get_entity (ARES GET /ekonomicke-subjekty/{ico})."""
    registrations = data.get("seznamRegistraci") or {}
    active = [name for key, name in REGISTERS.items()
              if registrations.get(f"stavZdroje{key}") == "AKTIVNI"]
    legal_form = data.get("pravniForma")
    return {
        "ico": data.get("ico"),
        "nazev": data.get("obchodniJmeno"),
        "pravni_forma": LEGAL_FORMS.get(legal_form, legal_form),
        "sidlo": (data.get("sidlo") or {}).get("textovaAdresa"),
        "datum_vzniku": data.get("datumVzniku"),
        "datum_zaniku": data.get("datumZaniku"),
        "zanikla": bool(data.get("datumZaniku")),
        "dic": data.get("dic"),
        "nace": data.get("czNace2008") or data.get("czNace") or [],
        "aktivni_registrace": active,
        "v_insolvencnim_rejstriku": registrations.get("stavZdrojeIr") == "AKTIVNI",
        "datum_aktualizace": data.get("datumAktualizace"),
    }


def summarize_search(data: dict[str, Any]) -> dict[str, Any]:
    """Page from search_entities: {total, start, count, items, next_start}."""
    return {
        "pocet_celkem": data.get("total", 0),
        "subjekty": [
            {
                "ico": s.get("ico"),
                "nazev": s.get("obchodniJmeno"),
                "sidlo": (s.get("sidlo") or {}).get("textovaAdresa"),
                "datum_zaniku": s.get("datumZaniku"),
            }
            for s in data.get("items") or []
        ],
    }


def _bodies(bodies: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result = []
    for body in _current(bodies):
        members = []
        for member in _current(body.get("clenoveOrganu")):
            function = ((member.get("clenstvi") or {}).get("funkce") or {}).get("nazev")
            members.append({
                "jmeno": _person_name(member),
                "funkce": function or member.get("nazevAngazma"),
                "od": member.get("datumZapisu"),
            })
        result.append({"organ": body.get("nazevOrganu"), "clenove": members})
    return result


def summarize_register(data: dict[str, Any]) -> dict[str, Any]:
    """Current state from get_register_record with register "vr".

    Only records without a deletion date are kept; the full history is long.
    """
    records = data.get("zaznamy") or []
    record = next((r for r in records if r.get("primarniZaznam")), records[0] if records else {})
    file_numbers = [
        f"{s.get('oddil')} {s.get('vlozka')} vedená u {s.get('soud')}"
        for s in _current(record.get("spisovaZnacka"))
    ]
    names = record.get("obchodniJmeno") or []
    former_names = [n.get("hodnota") for n in names if n.get("datumVymazu")]

    shareholders = []
    for group in _current(record.get("spolecnici")):
        for partner in _current(group.get("spolecnik")):
            if (partner.get("osoba") or {}).get("datumVymazu"):
                continue
            shares = _current(partner.get("podil"))
            share = shares[0] if shares else {}
            shareholders.append({
                "jmeno": _person_name(partner.get("osoba") or {}),
                "vklad": _amount(share.get("vklad")),
                "podil": _amount(share.get("velikostPodilu")),
            })
    for group in _current(record.get("akcionari")):
        for holder in _current(group.get("clenoveOrganu")):
            shareholders.append({"jmeno": _person_name(holder), "vklad": None, "podil": None})

    capital = _current(record.get("zakladniKapital"))
    return {
        "ico": data.get("icoId"),
        "rejstrik": record.get("rejstrik"),
        "spisova_znacka": file_numbers,
        "stav": record.get("stavSubjektu"),
        "datum_zapisu": record.get("datumZapisu"),
        "datum_vymazu": record.get("datumVymazu"),
        "predchozi_nazvy": former_names,
        "zakladni_kapital": _amount(capital[0].get("vklad")) if capital else None,
        "statutarni_organy": _bodies(record.get("statutarniOrgany")),
        "dozorci_a_jine_organy": _bodies(record.get("ostatniOrgany")),
        "spolecnici_a_akcionari": shareholders,
        "insolvence": [i.get("text") or i for i in _current(record.get("insolvence"))],
        "konkursy": [k.get("text") or k for k in _current(record.get("konkursy"))],
    }


SUMMARIZERS = {
    "get_entity": summarize_subject,
    "search_entities": summarize_search,
    "get_register_record": summarize_register,
}

# Other registers also hold personal data and have no summarizer here.
REGISTER_ONLY = "vr"


def _text(message: ToolMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))


def _data(message: ToolMessage) -> dict[str, Any]:
    """Tool result as a dict: the structured content, or the JSON text."""
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    if isinstance(data := artifact.get("structured_content"), dict):
        return data
    return json.loads(_text(message))


def compact_tool(tool: BaseTool) -> BaseTool:
    """Same name, description and arguments; the result is the compact summary as JSON.

    Errors of the server (for example `[not_found]`) reach the model as a tool error.
    """
    summarize = SUMMARIZERS[tool.name]

    async def call(**kwargs: Any) -> str:
        if (tool.name == "get_register_record"
                and str(kwargs.get("register", "")).strip().lower() != REGISTER_ONLY):
            raise ToolException(f"Only register {REGISTER_ONLY!r} (commercial register) is "
                                "available. Use get_entity for basic data.")
        message = await tool.ainvoke(
            {"name": tool.name, "args": kwargs, "id": "compact", "type": "tool_call"}
        )
        if message.status == "error":
            raise ToolException(_text(message))
        return json.dumps(summarize(_data(message)), ensure_ascii=False)

    return StructuredTool.from_function(
        coroutine=call,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        handle_tool_error=True,
    )


def compact_ares_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [compact_tool(tool) for tool in tools]
