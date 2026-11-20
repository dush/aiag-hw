"""Langflow component: read-only search tools over the RUIAN address database, for an Agent.

Connect its Toolset output to the `tools` input of an Agent (see ``flows/ruian-agent.json``).
It gives six tools: search_municipality, search_prague_district, search_city_district,
search_municipality_part, search_street and search_address_place.

Source of truth for the node embedded in that flow. The file must stay self-contained, because
Langflow runs the embedded code string, not an import. Rebuild the flow after editing this file:
``uv run python scripts/build_ruian_agent_flow.py``.

API names are English and keys are called `id`. The database keeps the Czech names (`kod`, `nazev`,
...) from scripts/ruian_schema.py. All SQL fragments come from the constants in this
module. User values are only passed as bound parameters (%(name)s), so no user input reaches the
SQL text. Every connection is read-only (default_transaction_read_only) with a statement timeout.
"""

import json
import os
import re
from dataclasses import dataclass, field
from typing import Annotated, Literal

from langchain_core.tools import StructuredTool, ToolException
from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, Output
from pydantic import BeforeValidator, Field, create_model

ENV_DATABASE_URL = "RUIAN_DATABASE_URL"
STATEMENT_TIMEOUT_MS = 5000
MAX_LIMIT = 200
MAX_IDS = 1000
CZ = 'COLLATE "cs-CZ-x-icu"'

# tsquery from free text, built in SQL with the same ruian_norm() as the indexed column:
# every word becomes a prefix term, all words must match ("nadraz br" -> 'nadraz':* & 'br':*).
TSQUERY = (
    "to_tsquery('simple', (SELECT string_agg(w || ':*', ' & ') "
    "FROM regexp_split_to_table(ruian_norm(%(name_match)s), '[^a-z0-9]+') AS w WHERE w <> ''))"
)
NORM = "ruian_norm(btrim(%(name_match)s))"
# LIKE pattern "<normalized query>%" with LIKE wildcards in the query escaped.
PREFIX = rf"replace(replace(replace({NORM}, '\', '\\'), '%%', '\%%'), '_', '\_') || '%%'"


class QueryError(ValueError):
    """Invalid tool arguments. The message is shown to the agent."""


# -- SQL builder (pure Python, no database access) ---------------------------------


@dataclass(frozen=True)
class Entity:
    """One searchable table. `select` maps API keys to SQL expressions."""

    tool: str
    from_: str
    select: dict[str, str]
    filters: dict[str, str]  # API parameter -> SQL column compared with = ANY(...)
    sorts: dict[str, tuple[str, ...]]  # API sort key -> SQL expressions
    name_col: str | None = "t.nazev"
    tsv_col: str | None = "t.nazev_tsv"
    default_sort: tuple[str, ...] = ("name",)
    require_filter: bool = False
    scalar_filters: dict[str, str] = field(default_factory=dict)  # API parameter -> SQL column, = value
    # Array cast per filter, so the comparison matches the column type and can use its index.
    filter_casts: dict[str, str] = field(default_factory=dict)

    @property
    def sort_keys(self) -> list[str]:
        keys = list(self.sorts)
        if self.tsv_col:
            keys.insert(0, "relevance")
        return keys


def _named() -> dict[str, tuple[str, ...]]:
    return {"name": (f"t.nazev {CZ}",), "id": ("t.kod",)}


MUNICIPALITY = Entity(
    tool="search_municipality",
    from_="obec t",
    select={"id": "t.kod", "name": "t.nazev"},
    filters={},
    sorts=_named(),
)

PRAGUE_DISTRICT = Entity(
    tool="search_prague_district",
    from_="obvod_prahy t JOIN obec o ON o.kod = t.obec_kod",
    select={"id": "t.kod", "name": "t.nazev", "municipality_id": "o.kod", "municipality_name": "o.nazev"},
    filters={"municipality_id_in": "t.obec_kod"},
    sorts={**_named(), "municipality_id": ("t.obec_kod",)},
)

CITY_DISTRICT = Entity(
    tool="search_city_district",
    from_=(
        "momc t JOIN obec o ON o.kod = t.obec_kod "
        "LEFT JOIN obvod_prahy op ON op.kod = t.obvod_prahy_kod"
    ),
    select={
        "id": "t.kod", "name": "t.nazev",
        "municipality_id": "o.kod", "municipality_name": "o.nazev",
        "prague_district_id": "op.kod", "prague_district_name": "op.nazev",
    },
    filters={"municipality_id_in": "t.obec_kod", "prague_district_id_in": "t.obvod_prahy_kod"},
    sorts={**_named(), "municipality_id": ("t.obec_kod",), "prague_district_id": ("t.obvod_prahy_kod",)},
)

MUNICIPALITY_PART = Entity(
    tool="search_municipality_part",
    from_="cast_obce t JOIN obec o ON o.kod = t.obec_kod",
    select={"id": "t.kod", "name": "t.nazev", "municipality_id": "o.kod", "municipality_name": "o.nazev"},
    filters={"municipality_id_in": "t.obec_kod"},
    sorts={**_named(), "municipality_id": ("t.obec_kod",)},
)

STREET = Entity(
    tool="search_street",
    from_="ulice t JOIN obec o ON o.kod = t.obec_kod",
    select={"id": "t.kod", "name": "t.nazev", "municipality_id": "o.kod", "municipality_name": "o.nazev"},
    filters={"municipality_id_in": "t.obec_kod"},
    sorts={**_named(), "municipality_id": ("t.obec_kod",)},
)

ADDRESS_PLACE = Entity(
    tool="search_address_place",
    from_=(
        "adresni_misto t "
        "JOIN obec o ON o.kod = t.obec_kod "
        "JOIN cast_obce co ON co.kod = t.cast_obce_kod "
        "LEFT JOIN ulice u ON u.kod = t.ulice_kod "
        "LEFT JOIN momc m ON m.kod = t.momc_kod "
        "LEFT JOIN obvod_prahy op ON op.kod = t.obvod_prahy_kod"
    ),
    select={
        "id": "t.kod",
        "building_number_type": "CASE t.typ_so WHEN 'č.ev.' THEN 'registration' ELSE 'descriptive' END",
        "building_number": "t.cislo_domovni",
        "orientation_number": "t.cislo_orientacni",
        "orientation_number_letter": "t.znak_cisla_orientacniho",
        "postal_code": "t.psc",
        "street_id": "u.kod", "street_name": "u.nazev",
        "municipality_part_id": "co.kod", "municipality_part_name": "co.nazev",
        "city_district_id": "m.kod", "city_district_name": "m.nazev",
        "prague_district_id": "op.kod", "prague_district_name": "op.nazev",
        "municipality_id": "o.kod", "municipality_name": "o.nazev",
        "sjtsk_x": "t.souradnice_x::float8", "sjtsk_y": "t.souradnice_y::float8",
        "valid_from": "t.plati_od::text",
    },
    filters={
        "municipality_id_in": "t.obec_kod",
        "municipality_part_id_in": "t.cast_obce_kod",
        "street_id_in": "t.ulice_kod",
        "city_district_id_in": "t.momc_kod",
        "prague_district_id_in": "t.obvod_prahy_kod",
        "postal_code_in": "t.psc",
    },
    scalar_filters={"building_number": "t.cislo_domovni", "orientation_number": "t.cislo_orientacni"},
    filter_casts={"postal_code_in": "::bpchar[]"},  # psc is char(5); a text[] would force a seq scan
    sorts={
        # Addresses without a street sort by their municipality part, like they are written.
        "street": (f"coalesce(u.nazev, co.nazev) {CZ}",),
        "building_number": ("t.cislo_domovni",),
        "orientation_number": ("t.cislo_orientacni", f"t.znak_cisla_orientacniho {CZ}"),
        "postal_code": ("t.psc",),
        "id": ("t.kod",),
    },
    name_col=None,
    tsv_col=None,
    default_sort=("street", "orientation_number", "building_number"),
    require_filter=True,
)

ENTITIES = {e.tool: e for e in (
    MUNICIPALITY, PRAGUE_DISTRICT, CITY_DISTRICT, MUNICIPALITY_PART, STREET, ADDRESS_PLACE,
)}


def _check_ids(name: str, values: list) -> None:
    if len(values) > MAX_IDS:
        raise QueryError(f"{name}: at most {MAX_IDS} values, got {len(values)}.")


def build_search(
    entity: Entity,
    *,
    name_match: str | None = None,
    id_in: list[int] | None = None,
    filters: dict[str, list | None] | None = None,
    scalars: dict[str, int | None] | None = None,
    sort: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[str, dict]:
    """Return (sql, params). The query fetches limit + 1 rows so the caller can set has_more."""
    params: dict = {}
    where: list[str] = []

    if name_match is not None:
        if entity.tsv_col is None:
            raise QueryError(f"{entity.tool} does not support name_match.")
        if not re.search(r"\w", name_match):
            raise QueryError("name_match must contain at least one letter or digit.")
        params["name_match"] = name_match
        where.append(f"{entity.tsv_col} @@ {TSQUERY}")

    if id_in is not None:
        _check_ids("id_in", id_in)
        params["id_in"] = list(id_in)
        where.append("t.kod = ANY(%(id_in)s)")

    for key, values in (filters or {}).items():
        if values is None:
            continue
        if key not in entity.filters:
            raise QueryError(f"{entity.tool} has no filter {key!r}.")
        _check_ids(key, values)
        params[key] = list(values)
        where.append(f"{entity.filters[key]} = ANY(%({key})s{entity.filter_casts.get(key, '')})")

    for key, value in (scalars or {}).items():
        if value is None:
            continue
        if key not in entity.scalar_filters:
            raise QueryError(f"{entity.tool} has no filter {key!r}.")
        params[key] = value
        where.append(f"{entity.scalar_filters[key]} = %({key})s")

    if entity.require_filter and not where:
        raise QueryError(f"{entity.tool} needs at least one filter.")

    if not 1 <= limit <= MAX_LIMIT:
        raise QueryError(f"limit must be between 1 and {MAX_LIMIT}.")
    if offset < 0:
        raise QueryError("offset must be 0 or more.")

    order = _order_by(entity, sort, has_name_match=name_match is not None, has_id_in=id_in is not None)

    cols = ", ".join(f'{expr} AS "{key}"' for key, expr in entity.select.items())
    sql = f"SELECT {cols} FROM {entity.from_}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s"
    params["limit"] = limit + 1
    params["offset"] = offset
    return sql, params


def _relevance(entity: Entity) -> list[str]:
    # Exact name first, then names starting with the query, then ts_rank (normalized by length).
    return [
        f"(ruian_norm({entity.name_col}) = {NORM}) DESC",
        f"(ruian_norm({entity.name_col}) LIKE {PREFIX}) DESC",
        f"ts_rank({entity.tsv_col}, {TSQUERY}, 1) DESC",
    ]


def _order_by(entity: Entity, sort: list[str] | None, *, has_name_match: bool, has_id_in: bool) -> str:
    parts: list[str] = []
    if sort:
        keys = sort
    elif has_name_match:
        keys = ["relevance", *entity.default_sort]
    elif has_id_in:
        return "array_position(%(id_in)s::int[], t.kod)"
    else:
        keys = list(entity.default_sort)

    for raw in keys:
        desc = raw.startswith("-")
        key = raw[1:] if desc else raw
        if key == "relevance":
            if entity.tsv_col is None:
                raise QueryError(f"{entity.tool} cannot sort by relevance.")
            if not has_name_match:
                raise QueryError("Sort by relevance needs name_match.")
            terms = _relevance(entity)
            if desc:  # "-relevance": least relevant first
                terms = [t.removesuffix(" DESC") for t in terms]
            parts.extend(terms)
            continue
        if key not in entity.sorts:
            raise QueryError(f"Unknown sort key {raw!r}. Allowed: {', '.join(entity.sort_keys)} (prefix '-' for descending).")
        direction = " DESC" if desc else ""
        parts.extend(f"{expr}{direction}" for expr in entity.sorts[key])
    parts.append("t.kod")  # deterministic order for offset paging
    return ", ".join(parts)


def format_address(row: dict) -> str:
    """Format an address place the way Czech addresses are written.

    "Vodičkova 681/36, Nové Město, 110 00 Praha 1" or, without a street, "Lhota 25, 511 01 Turnov".
    The municipality part is left out when it has the same name as the municipality.
    """
    number = str(row["building_number"])
    if row["building_number_type"] == "registration":
        number = f"č. ev. {number}"
    if row["orientation_number"] is not None:
        number += f"/{row['orientation_number']}{row['orientation_number_letter'] or ''}"

    town = row["prague_district_name"] or row["municipality_name"]
    psc = row["postal_code"]
    psc = f"{psc[:3]} {psc[3:]}" if psc and len(psc) == 5 else psc
    part = row["municipality_part_name"]

    if row["street_name"]:
        lines = [f"{row['street_name']} {number}"]
        if part and part != row["municipality_name"]:
            lines.append(part)
    else:
        lines = [f"{part} {number}"]
    lines.append(f"{psc} {town}")
    return ", ".join(lines)


# -- tool definitions -------------------------------------------------------------

NAME_MATCH_HELP = (
    "Fulltext search on the name. Case and diacritics are ignored; every word is a prefix and all words must match."
)
SORT_HELP = (
    "Sort keys, applied in order; prefix '-' for descending. Default: relevance when name_match is set, "
    "else the order of id_in when only id_in is set, else name."
)
ADDRESS_SORT_HELP = (
    "Sort keys, applied in order; prefix '-' for descending. Default: the order of id_in when only id_in "
    "is set, else street, orientation_number, building_number."
)
FILTER_HELP = {
    "municipality_id_in": "Only items in these municipalities (municipality ids).",
    "prague_district_id_in": "Only items in these Prague districts (ids).",
    "municipality_part_id_in": "Only addresses in these municipality parts (ids).",
    "street_id_in": "Only addresses in these streets (ids).",
    "city_district_id_in": "Only addresses in these city districts (ids).",
    "postal_code_in": "Only addresses with these postal codes, 5 digits without a space, e.g. '11000'.",
}
SCALAR_HELP = {
    "building_number": "Building number (číslo popisné or číslo evidenční), the first number in '681/36'.",
    "orientation_number": "Orientation number (číslo orientační), the second number in '681/36'.",
}
TOOL_DESCRIPTIONS = {
    "search_municipality": "Search municipalities (obec), for example 'Brno' or 'Praha'. Returns id and name.",
    "search_prague_district": "Search the 10 Prague districts (obvod Prahy), 'Praha 1' to 'Praha 10'.",
    "search_city_district": (
        "Search city districts (MOMC: městský obvod / městská část) of Prague and other divided cities, "
        "for example 'Praha 6' or 'Brno-střed'."
    ),
    "search_municipality_part": (
        "Search municipality parts (část obce), for example 'Nové Město' in Praha or a village belonging to a town."
    ),
    "search_street": (
        "Search streets (ulice). Many municipalities have streets with the same name, so filter by "
        "municipality_id_in when you know the town."
    ),
    "search_address_place": (
        "Search address places (adresní místo). Address places have no name: first find the street, "
        "municipality part or municipality with the other search_* tools, then filter by its id. At least one "
        "filter is required. Each item has a ready-to-show 'formatted' address."
    ),
}
RESULT_HELP = ' Returns JSON {"items": [...], "has_more": bool, "limit": n, "offset": n}.'


def _as_list(value):
    """Accept a single value where a list is expected: models often send 478156 instead of [478156]."""
    return [value] if value is not None and not isinstance(value, (list, tuple)) else value


def list_of(item_type):
    return Annotated[list[item_type] | None, BeforeValidator(_as_list)]


def args_schema(entity: Entity):
    """Pydantic model of one tool's arguments.

    Built with create_model and real types: Langflow compiles component code with postponed
    annotations, so string annotations in a class body might not resolve.
    """
    fields: dict = {}
    if entity.tsv_col:
        fields["name_match"] = (str | None, Field(default=None, description=NAME_MATCH_HELP))
    fields["id_in"] = (list_of(int), Field(default=None, description="Return only these ids (exact primary keys)."))
    for key in entity.filters:
        item_type = str if key == "postal_code_in" else int
        fields[key] = (list_of(item_type), Field(default=None, description=FILTER_HELP[key]))
    for key in entity.scalar_filters:
        fields[key] = (int | None, Field(default=None, description=SCALAR_HELP[key]))
    sort_values = tuple(v for key in entity.sort_keys for v in (key, f"-{key}"))
    sort_help = SORT_HELP if entity.tsv_col else ADDRESS_SORT_HELP
    fields["sort"] = (list_of(Literal[sort_values]), Field(default=None, description=sort_help))
    fields["limit"] = (int, Field(default=20, ge=1, le=MAX_LIMIT, description=f"Maximum number of items (1-{MAX_LIMIT})."))
    fields["offset"] = (int, Field(default=0, ge=0, description="Number of items to skip, for paging."))
    model_name = "".join(part.title() for part in entity.tool.split("_")) + "Args"
    return create_model(model_name, **fields)


def search_kwargs(entity: Entity, args: dict) -> dict:
    """Map tool arguments to build_search() keyword arguments."""
    filters = {key: args.get(key) for key in entity.filters}
    if filters.get("postal_code_in"):
        filters["postal_code_in"] = [p.replace(" ", "") for p in filters["postal_code_in"]]
    return {
        "name_match": args.get("name_match"),
        "id_in": args.get("id_in"),
        "filters": filters,
        "scalars": {key: args.get(key) for key in entity.scalar_filters},
        "sort": args.get("sort"),
        "limit": args.get("limit", 20),
        "offset": args.get("offset", 0),
    }


def run_search(conninfo: str, entity: Entity, args: dict) -> str:
    """Run one search and return the result as JSON text. Errors become ToolException for the agent."""
    kwargs = search_kwargs(entity, args)
    try:
        sql, params = build_search(entity, **kwargs)
    except QueryError as e:
        raise ToolException(str(e)) from e

    import psycopg
    from psycopg.rows import dict_row

    try:
        with psycopg.connect(conninfo, row_factory=dict_row, autocommit=True) as conn:
            rows = conn.execute(sql, params).fetchall()
    except psycopg.errors.QueryCanceled as e:
        msg = f"The search took longer than {STATEMENT_TIMEOUT_MS // 1000} s. Add filters or a longer name_match."
        raise ToolException(msg) from e
    except psycopg.Error as e:
        msg = f"Database error: {e}"
        raise ToolException(msg) from e
    limit = kwargs["limit"]
    has_more = len(rows) > limit
    rows = rows[:limit]
    if entity is ADDRESS_PLACE:
        for row in rows:
            row["formatted"] = format_address(row)
    result = {"items": rows, "has_more": has_more, "limit": limit, "offset": kwargs["offset"]}
    return json.dumps(result, ensure_ascii=False, default=str)


def validation_message(error) -> str:
    """Short text of a pydantic ValidationError, for example "limit: Input should be less than or equal to 200"."""
    errors = getattr(error, "errors", None)
    if not callable(errors):
        return f"Invalid arguments: {error}"
    parts = [f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in errors()]
    return "Invalid arguments: " + "; ".join(parts)


def make_tool(conninfo: str, entity: Entity) -> StructuredTool:
    def search(**args) -> str:
        return run_search(conninfo, entity, args)

    return StructuredTool.from_function(
        func=search,
        name=entity.tool,
        description=TOOL_DESCRIPTIONS[entity.tool] + RESULT_HELP,
        args_schema=args_schema(entity),
        # Bad arguments and query errors go back to the agent as the tool result, not as a failed run.
        handle_tool_error=True,
        handle_validation_error=validation_message,
    )


class RuianSearchComponent(Component):
    display_name = "RUIAN Search"
    description = (
        "Read-only search tools over the Czech RUIAN address register (municipalities, Prague districts, "
        "city districts, municipality parts, streets, address places). Connect the Toolset to an Agent."
    )
    icon = "map-pin"
    name = "RuianSearch"

    inputs = [
        MessageTextInput(
            name="database_url",
            display_name="Database URL",
            info=(
                "PostgreSQL URL of the RUIAN database, e.g. postgresql://user:pass@host:5432/ruian. "
                f"Empty: use the {ENV_DATABASE_URL} environment variable, else the compose Postgres "
                "(host postgres, user langflow, POSTGRES_PASSWORD, database ruian)."
            ),
            value="",
            advanced=True,
        ),
    ]
    outputs = [
        Output(display_name="Toolset", name="component_as_tool", method="to_toolkit", types=["Tool"], cache=False),
    ]

    def _conninfo(self) -> str:
        from psycopg.conninfo import make_conninfo

        # Compose passes RUIAN_DATABASE_URL as an empty string when it is not set.
        url = (self.database_url or "").strip() or os.environ.get(ENV_DATABASE_URL, "").strip()
        if not url:
            password = os.environ.get("POSTGRES_PASSWORD", "")
            if not password:
                msg = f"No database configured: fill Database URL, set {ENV_DATABASE_URL} or POSTGRES_PASSWORD."
                raise ValueError(msg)
            url = make_conninfo(host="postgres", port=5432, user="langflow", password=password, dbname="ruian")
        return make_conninfo(
            url,
            options=f"-c default_transaction_read_only=on -c statement_timeout={STATEMENT_TIMEOUT_MS}",
            application_name="langflow-ruian-search",
            connect_timeout=10,
        )

    def _get_tools(self) -> list[StructuredTool]:
        conninfo = self._conninfo()
        return [make_tool(conninfo, entity) for entity in ENTITIES.values()]
