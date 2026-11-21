"""Langflow component: read-only search tools over the RUIAN address database, for an Agent.

Connect its Toolset output to the `tools` input of an Agent (see ``flows/ruian-agent.json``).
It gives nine tools: search_municipality, search_prague_district, search_city_district,
search_municipality_part, search_street, search_address_place (by ids and numbers), search_address
(a written address, over the full-address word index adresni_misto.adresa_tsv), search_nearby and
address_distances (straight-line distances on adresni_misto.poloha, a point in S-JTSK metres). Results
carry the total number of matches; address places also WGS84 lat/lon, converted from S-JTSK here.

Source of truth for the node embedded in that flow. The file must stay self-contained, because
Langflow runs the embedded code string, not an import. Rebuild the flow after editing this file:
``uv run python scripts/build_ruian_agent_flow.py``.

API names are English and keys are called `id`. The database keeps the Czech names (`kod`, `nazev`,
...) from scripts/ruian_schema.py. All SQL fragments come from the constants in this
module. User values are only passed as bound parameters (%(name)s), so no user input reaches the
SQL text. Every connection is read-only (default_transaction_read_only) with a statement timeout.
"""

import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass, field, replace
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
    count_cap: int = 100_000  # total is exact up to this many items
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
    count_cap=10_000,  # 3 million rows: an exact count of a broad filter could hit the statement timeout
)

ENTITIES = {e.tool: e for e in (
    MUNICIPALITY, PRAGUE_DISTRICT, CITY_DISTRICT, MUNICIPALITY_PART, STREET, ADDRESS_PLACE,
)}


def _check_ids(name: str, values: list) -> None:
    if len(values) > MAX_IDS:
        raise QueryError(f"{name}: at most {MAX_IDS} values, got {len(values)}.")


def _where(
    entity: Entity,
    *,
    name_match: str | None = None,
    name_mode: str = "word_prefix",
    id_in: list[int] | None = None,
    filters: dict[str, list | None] | None = None,
    scalars: dict[str, int | None] | None = None,
) -> tuple[list[str], dict]:
    """WHERE conditions (on the base table `t` only) and their parameters."""
    params: dict = {}
    where: list[str] = []

    if name_match is not None:
        if entity.tsv_col is None:
            raise QueryError(f"{entity.tool} does not support name_match.")
        if not re.search(r"\w", name_match):
            raise QueryError("name_match must contain at least one letter or digit.")
        if name_mode not in NAME_MODES:
            raise QueryError(f"name_mode must be one of {', '.join(NAME_MODES)}.")
        params["name_match"] = name_match
        where.append(f"{entity.tsv_col} @@ {TSQUERY}")  # keeps the GIN index in use for every mode
        if name_mode == "starts_with":
            where.append(f"ruian_norm({entity.name_col}) LIKE {PREFIX}")
        elif name_mode == "exact":
            where.append(f"ruian_norm({entity.name_col}) = {NORM}")

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
    return where, params


def _check_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise QueryError(f"limit must be between 1 and {MAX_LIMIT}.")
    if offset < 0:
        raise QueryError("offset must be 0 or more.")


def _select(entity: Entity, where: list[str], order: str, extra: dict[str, str] | None = None) -> str:
    cols = ", ".join(f'{expr} AS "{key}"' for key, expr in {**entity.select, **(extra or {})}.items())
    sql = f"SELECT {cols} FROM {entity.from_}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return sql + f" ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s"


def build_count(entity: Entity, where: list[str]) -> str:
    """Count of the filtered items, stopping at entity.count_cap + 1 rows (then the total is a lower bound).

    Runs on the base table only (all conditions use `t`), so it skips the joins of the item query.
    """
    base = entity.from_.split(" JOIN ")[0]
    sql = f"SELECT 1 FROM {base}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return f"SELECT count(*) AS n FROM ({sql} LIMIT {entity.count_cap + 1}) s"


def build_search(
    entity: Entity,
    *,
    name_match: str | None = None,
    name_mode: str = "word_prefix",
    id_in: list[int] | None = None,
    filters: dict[str, list | None] | None = None,
    scalars: dict[str, int | None] | None = None,
    sort: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[str, dict]:
    """Return (sql, params). The query fetches limit + 1 rows so the caller can set has_more."""
    where, params = _where(entity, name_match=name_match, name_mode=name_mode, id_in=id_in, filters=filters,
                           scalars=scalars)
    _check_page(limit, offset)
    order = _order_by(entity, sort, has_name_match=name_match is not None, has_id_in=id_in is not None)
    params["limit"] = limit + 1
    params["offset"] = offset
    return _select(entity, where, order), params


# Words of a free-text address that are not part of names or numbers ("č. p. 5", "ulice", "PSČ").
ADDRESS_STOPWORDS = {
    "c", "p", "cp", "co", "ce", "ev", "cislo", "popisne", "orientacni", "evidencni", "psc", "ul", "ulice",
    "adresa", "dum",
}


def fold(text: str) -> str:
    """Lowercase without diacritics, like ruian_norm() in the database (Czech letters)."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def address_terms(query: str) -> list[str]:
    """Search terms of a free-text address, split like the indexed words (ruian_address_words)."""
    query = re.sub(r"\b(\d{3})\s+(\d{2})\b", r"\1\2", query)  # postal code "110 00" -> "11000"
    words = re.split(r"[^a-z0-9]+", fold(query))
    return [w for w in words if w and w not in ADDRESS_STOPWORDS and not (len(w) == 1 and w.isalpha())]


def build_address_search(
    query: str, *, municipality_id_in: list[int] | None = None, limit: int = 20, offset: int = 0
) -> tuple[str, dict, list[str]]:
    """Return (sql, params, where) for search_address.

    Words match as prefixes, numbers ('36', '704', '36a', '11000') only exactly. Addresses whose
    orientation number, then building number, equals a number of the query come first.
    """
    terms = address_terms(query)
    if not terms:
        raise QueryError("query must contain a street, place or municipality name, or a number.")
    if len(terms) > 12:
        raise QueryError("query is too long: use at most 12 words and numbers.")
    _check_page(limit, offset)
    tsquery = " & ".join(f"'{w}'" if any(ch.isdigit() for ch in w) else f"'{w}':*" for w in terms)
    numbers = [int(w) for w in terms if w.isdigit() and len(w) <= 6]
    params: dict = {"address_query": tsquery, "numbers": numbers}
    where = ["t.adresa_tsv @@ to_tsquery('simple', %(address_query)s)"]
    if municipality_id_in:
        _check_ids("municipality_id_in", municipality_id_in)
        params["municipality_id_in"] = list(municipality_id_in)
        where.append("t.obec_kod = ANY(%(municipality_id_in)s)")
    order = ", ".join([
        "coalesce(t.cislo_orientacni = ANY(%(numbers)s::int[]), false) DESC",
        "(t.cislo_domovni = ANY(%(numbers)s::int[])) DESC",
        *(expr for key in ADDRESS_PLACE.default_sort for expr in ADDRESS_PLACE.sorts[key]),
        "t.kod",
    ])
    params["limit"] = limit + 1
    params["offset"] = offset
    return _select(ADDRESS_PLACE, where, order), params, where


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


def sjtsk_to_wgs84(y: float, x: float, height: float = 245.0) -> tuple[float, float]:
    """(lat, lon) in WGS84 degrees for RUIAN S-JTSK coordinates (souradnice_y, souradnice_x; positive values).

    Inverse Křovák projection on the Bessel ellipsoid, 7-parameter Helmert transformation to WGS84, then
    geodetic coordinates on the WGS84 ellipsoid. Accuracy about 1-2 m (enough for maps; checked against
    pyproj EPSG:5514 -> EPSG:4326 in tests).
    """
    # Křovák projection constants (S-JTSK).
    a, e, n = 6377397.15508, 0.081696831215303, 0.97992470462083
    rho0 = 12310230.12797036
    sin_uq, cos_uq = 0.863499969506341, 0.504348889819882
    sin_vq, cos_vq = 0.420215144586493, 0.907424504992097
    alpha, k = 1.000597498371542, 1.003419163966575

    rho = math.hypot(x, y)
    eps = 2 * math.atan(y / (rho + x))
    d = eps / n
    s = 2 * math.atan(math.exp(math.log(rho0 / rho) / n)) - math.pi / 2
    sin_s, cos_s = math.sin(s), math.cos(s)
    sin_u = sin_uq * sin_s - cos_uq * cos_s * math.cos(d)
    cos_u = math.sqrt(1 - sin_u * sin_u)
    sin_dv = math.sin(d) * cos_s / cos_u
    cos_dv = math.sqrt(1 - sin_dv * sin_dv)
    sin_v = sin_vq * cos_dv - cos_vq * sin_dv
    cos_v = cos_vq * cos_dv + sin_vq * sin_dv
    lon_b = 2 * math.atan(sin_v / (1 + cos_v)) / alpha
    t = math.exp(2 / alpha * math.log((1 + sin_u) / cos_u / k))
    p = (t - 1) / (t + 1)
    for _ in range(50):
        sin_b = p
        p = t * math.exp(e * math.log((1 + e * sin_b) / (1 - e * sin_b)))
        p = (p - 1) / (p + 1)
        if abs(p - sin_b) < 1e-15:
            break
    lat_b = math.atan(p / math.sqrt(1 - p * p))

    # Bessel geodetic -> cartesian.
    f_1 = 299.152812853
    e2 = 1 - (1 - 1 / f_1) ** 2
    nu = a / math.sqrt(1 - e2 * math.sin(lat_b) ** 2)
    cx = (nu + height) * math.cos(lat_b) * math.cos(lon_b)
    cy = (nu + height) * math.cos(lat_b) * math.sin(lon_b)
    cz = ((1 - e2) * nu + height) * math.sin(lat_b)

    # Helmert S-JTSK -> WGS84.
    dx, dy, dz = 570.69, 85.69, 462.84
    arcsec = math.pi / 180 / 3600
    wx, wy, wz, m = -4.99821 * arcsec, -1.58676 * arcsec, -5.2611 * arcsec, 3.543e-6
    xn = dx + (1 + m) * (cx + wz * cy - wy * cz)
    yn = dy + (1 + m) * (-wz * cx + cy + wx * cz)
    zn = dz + (1 + m) * (wy * cx - wx * cy + cz)

    # Cartesian -> WGS84 geodetic (Bowring).
    a, f_1 = 6378137.0, 298.257223563
    a_b = f_1 / (f_1 - 1)
    e2 = 1 - (1 - 1 / f_1) ** 2
    pp = math.hypot(xn, yn)
    theta = math.atan(zn * a_b / pp)
    st, ct = math.sin(theta), math.cos(theta)
    lat = math.atan((zn + e2 * a_b * a * st**3) / (pp - e2 * a * ct**3))
    lon = 2 * math.atan(yn / (pp + xn))
    return math.degrees(lat), math.degrees(lon)


# -- tool definitions -------------------------------------------------------------

NAME_MODES = ("word_prefix", "starts_with", "exact")
NAME_MATCH_HELP = (
    "Name to search for, in the nominative (Brno, not Brně). Case and diacritics are ignored. "
    "Example: 'nadraz' finds 'Nádražní'."
)
NAME_MODE_HELP = (
    "How name_match is matched. word_prefix (default): every word of name_match is the start of some word "
    "of the name ('nadraz' finds 'Nádražní' and 'Nad nádražím'). starts_with: the whole name starts with "
    "name_match (only 'Nádražní'); use it for 'names starting with ...'. exact: the whole name equals "
    "name_match (only 'Lhota', not 'Horní Lhota')."
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
    "municipality_id_in": "Only items in these municipalities, e.g. [582786] for Brno (ids from search_municipality).",
    "prague_district_id_in": "Only items in these Prague districts (ids from search_prague_district).",
    "municipality_part_id_in": "Only addresses in these municipality parts (ids from search_municipality_part).",
    "street_id_in": "Only addresses in these streets, e.g. [478156] (ids from search_street).",
    "city_district_id_in": "Only addresses in these city districts (ids from search_city_district).",
    "postal_code_in": "Only addresses with these postal codes, e.g. ['11000'] (5 digits; spaces are removed).",
}
SCALAR_HELP = {
    "building_number": "Building number (číslo popisné or číslo evidenční): 704 in 'Vodičkova 704/36'.",
    "orientation_number": "Orientation number (číslo orientační): 36 in 'Vodičkova 704/36'.",
}
DETAIL_HELP = (
    "false (default): compact items (id, formatted address, numbers, postal code, parent ids, lat/lon). "
    "true: also the names of all parents, building_number_type, S-JTSK coordinates and valid_from."
)
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
        "Search address places (adresní místo) by ids and numbers: street_id_in, municipality_part_id_in, "
        "municipality_id_in, postal_code_in, building_number, orientation_number. At least one filter is required. "
        "For a written address such as 'Vodičkova 36, Praha', use search_address instead."
    ),
    "search_address": (
        "Find address places from a written address in one call, e.g. 'Vodičkova 36 Praha', 'Bukovina 5 679 05' "
        "or 'náměstí Svobody 1 Brno'. Every word must be the start of a word of the street, municipality part, "
        "municipality or district name; every number must equal the building number, orientation number "
        "(also with its letter: '36a') or postal code. Addresses whose orientation number, then building number, "
        "equals a number of the query come first. Leave out words that are not part of the address."
    ),
    "search_nearby": (
        "Find address places near an origin: an address place (address_id) or a WGS84 point (lat, lon), within "
        "radius_m metres (default 500, max 20000), nearest first. Each item has distance_m, the straight-line "
        "('as the crow flies') distance in metres, NOT the distance by road or on foot. Optional street_id_in, "
        "municipality_id_in or postal_code_in narrow the result, e.g. the nearest address on a given street. "
        "The origin address itself is not listed."
    ),
    "address_distances": (
        "Straight-line ('as the crow flies') distances in metres from one origin (from_address_id, or a WGS84 point "
        "lat/lon) to up to 100 other address places (to_address_ids), NOT the distance by road or on foot. "
        "Items keep the order of to_address_ids. Get address ids with search_address first."
    ),
}
RESULT_HELP = (
    ' Returns JSON {"items": [...], "total": n, "total_is_lower_bound": bool, "has_more": bool, "limit": n, '
    '"offset": n}. total is the number of all matching items (use it to answer "how many"); when '
    "total_is_lower_bound is true there are more than total."
)
DISTANCES_RESULT_HELP = (
    ' Returns JSON {"origin": ..., "items": [...], "not_found": [ids], "without_coordinates": [ids]}; each item has '
    "distance_m."
)
ADDRESS_ITEM_HELP = " Each item has a ready-to-show 'formatted' address and WGS84 'lat'/'lon'."
NULL_WORDS = {"", "null", "none", "nil", "undefined", "n/a"}

COMPACT_ADDRESS_KEYS = (
    "id", "formatted", "building_number", "orientation_number", "orientation_number_letter", "postal_code",
    "street_id", "municipality_part_id", "city_district_id", "prague_district_id", "municipality_id", "lat", "lon",
    "distance_m",
)


def _as_list(value):
    """Accept a single value where a list is expected: models often send 478156 instead of [478156]."""
    return [value] if value is not None and not isinstance(value, (list, tuple)) else value


def _blank_to_none(value):
    """Treat '', 'null', 'none' ... as not set: models sometimes send them instead of leaving a field out."""
    if isinstance(value, str) and value.strip().lower() in NULL_WORDS:
        return None
    return value


def list_of(item_type):
    return Annotated[list[item_type] | None, BeforeValidator(_as_list)]


OptionalText = Annotated[str | None, BeforeValidator(_blank_to_none)]


def _page_fields(fields: dict) -> None:
    fields["limit"] = (int, Field(default=20, ge=1, le=MAX_LIMIT, description=f"Maximum number of items (1-{MAX_LIMIT})."))
    fields["offset"] = (int, Field(default=0, ge=0, description="Number of items to skip, for paging."))


def args_schema(entity: Entity):
    """Pydantic model of one tool's arguments.

    Built with create_model and real types: Langflow compiles component code with postponed
    annotations, so string annotations in a class body might not resolve.
    """
    fields: dict = {}
    if entity.tsv_col:
        fields["name_match"] = (OptionalText, Field(default=None, description=NAME_MATCH_HELP))
        fields["name_mode"] = (Literal[NAME_MODES], Field(default="word_prefix", description=NAME_MODE_HELP))
    fields["id_in"] = (list_of(int), Field(default=None, description="Return only these ids (exact primary keys)."))
    for key in entity.filters:
        item_type = str if key == "postal_code_in" else int
        fields[key] = (list_of(item_type), Field(default=None, description=FILTER_HELP[key]))
    for key in entity.scalar_filters:
        fields[key] = (int | None, Field(default=None, description=SCALAR_HELP[key]))
    sort_values = tuple(v for key in entity.sort_keys for v in (key, f"-{key}"))
    sort_help = SORT_HELP if entity.tsv_col else ADDRESS_SORT_HELP
    fields["sort"] = (list_of(Literal[sort_values]), Field(default=None, description=sort_help))
    if entity is ADDRESS_PLACE:
        fields["detail"] = (bool, Field(default=False, description=DETAIL_HELP))
    _page_fields(fields)
    model_name = "".join(part.title() for part in entity.tool.split("_")) + "Args"
    return create_model(model_name, **fields)


def address_args_schema():
    fields: dict = {
        "query": (str, Field(description="Written address, e.g. 'Vodičkova 36 Praha' or 'Lhota 25 511 01'.")),
        "municipality_id_in": (list_of(int), Field(default=None, description=FILTER_HELP["municipality_id_in"])),
        "detail": (bool, Field(default=False, description=DETAIL_HELP)),
    }
    _page_fields(fields)
    return create_model("SearchAddressArgs", **fields)


def search_kwargs(entity: Entity, args: dict) -> dict:
    """Map tool arguments to build_search() keyword arguments."""
    filters = {key: args.get(key) for key in entity.filters}
    if filters.get("postal_code_in"):
        filters["postal_code_in"] = [p.replace(" ", "") for p in filters["postal_code_in"]]
    return {
        "name_match": _blank_to_none(args.get("name_match")),
        "name_mode": args.get("name_mode") or "word_prefix",
        "id_in": args.get("id_in"),
        "filters": filters,
        "scalars": {key: args.get(key) for key in entity.scalar_filters},
        "sort": args.get("sort"),
        "limit": args.get("limit", 20),
        "offset": args.get("offset", 0),
    }


def address_item(row: dict, *, detail: bool) -> dict:
    row["formatted"] = format_address(row)
    if row.get("sjtsk_x") is not None and row.get("sjtsk_y") is not None:
        lat, lon = sjtsk_to_wgs84(row["sjtsk_y"], row["sjtsk_x"])
        row["lat"], row["lon"] = round(lat, 6), round(lon, 6)
    if detail:
        return row
    return {k: row[k] for k in COMPACT_ADDRESS_KEYS if row.get(k) is not None}


def _db(conninfo: str, work) -> str:
    """Run work(connection) and return its result as JSON text. Query and database errors become ToolException."""
    import psycopg
    from psycopg.rows import dict_row

    try:
        with psycopg.connect(conninfo, row_factory=dict_row, autocommit=True) as conn:
            result = work(conn)
    except QueryError as e:
        raise ToolException(str(e)) from e
    except psycopg.errors.QueryCanceled as e:
        msg = f"The search took longer than {STATEMENT_TIMEOUT_MS // 1000} s. Add filters or a longer name."
        raise ToolException(msg) from e
    except psycopg.Error as e:
        msg = f"Database error: {e}"
        raise ToolException(msg) from e
    return json.dumps(result, ensure_ascii=False, default=str)


def _query_result(conn, entity: Entity, sql: str, params: dict, where: list[str], *, detail: bool) -> dict:
    """Items (limit + 1 fetched for has_more) and the capped total of one search."""
    rows = conn.execute(sql, params).fetchall()
    total = conn.execute(build_count(entity, where), params).fetchone()["n"]
    limit, offset = params["limit"] - 1, params["offset"]
    has_more = len(rows) > limit
    rows = rows[:limit]
    if entity.from_ == ADDRESS_PLACE.from_:
        rows = [address_item(row, detail=detail) for row in rows]
    return {
        "items": rows,
        "total": min(total, entity.count_cap),
        "total_is_lower_bound": total > entity.count_cap,
        "has_more": has_more,
        "limit": limit,
        "offset": offset,
    }


def _execute(conninfo: str, entity: Entity, sql: str, params: dict, where: list[str], *, detail: bool) -> str:
    return _db(conninfo, lambda conn: _query_result(conn, entity, sql, params, where, detail=detail))


def run_search(conninfo: str, entity: Entity, args: dict) -> str:
    """Run one search tool call and return the result as JSON text."""
    kwargs = search_kwargs(entity, args)
    try:
        sql, params = build_search(entity, **kwargs)
        where, _ = _where(entity, **{k: kwargs[k] for k in ("name_match", "name_mode", "id_in", "filters", "scalars")})
    except QueryError as e:
        raise ToolException(str(e)) from e
    return _execute(conninfo, entity, sql, params, where, detail=bool(args.get("detail")))


def run_address_search(conninfo: str, args: dict) -> str:
    query = _blank_to_none(args.get("query"))
    if query is None:
        raise ToolException("query is required, e.g. 'Vodičkova 36 Praha'.")
    try:
        sql, params, where = build_address_search(
            query, municipality_id_in=args.get("municipality_id_in"),
            limit=args.get("limit", 20), offset=args.get("offset", 0),
        )
    except QueryError as e:
        raise ToolException(str(e)) from e
    return _execute(conninfo, ADDRESS_PLACE, sql, params, where, detail=bool(args.get("detail")))


# -- distances (search_nearby, address_distances) ------------------------------------------------

NEARBY_DEFAULT_RADIUS_M = 500
NEARBY_MAX_RADIUS_M = 20_000
MAX_DISTANCE_TARGETS = 100
# Rough WGS84 bounds of the Czech Republic, to catch swapped or foreign coordinates.
CZ_LAT, CZ_LON = (48.5, 51.1), (12.0, 18.9)
# The position column is point(y, x) in S-JTSK; every centre and box corner uses the same order.
CENTER = "point(%(cy)s, %(cx)s)"
DISTANCE = f"(round(t.poloha <-> {CENTER}))::int"
CROW_FLIES = "straight-line ('as the crow flies') distance in metres, not by road or on foot"

NEARBY = replace(ADDRESS_PLACE, tool="search_nearby", require_filter=False)


def wgs84_to_sjtsk(lat: float, lon: float) -> tuple[float, float]:
    """(y, x) in S-JTSK (positive RUIAN values) for WGS84 lat/lon: Newton iteration on sjtsk_to_wgs84."""
    y, x = 700_000.0, 1_100_000.0  # about the centre of Czechia
    for _ in range(20):
        la, lo = sjtsk_to_wgs84(y, x)
        dlat, dlon = lat - la, lon - lo
        if abs(dlat) < 1e-10 and abs(dlon) < 1e-10:  # about 0.01 mm
            return y, x
        la_y, lo_y = sjtsk_to_wgs84(y + 1.0, x)
        la_x, lo_x = sjtsk_to_wgs84(y, x + 1.0)
        j11, j12, j21, j22 = la_y - la, la_x - la, lo_y - lo, lo_x - lo
        det = j11 * j22 - j12 * j21
        y += (j22 * dlat - j12 * dlon) / det
        x += (-j21 * dlat + j11 * dlon) / det
    raise ValueError(f"S-JTSK conversion of {lat}, {lon} did not converge.")


def check_cz_point(lat: float, lon: float) -> None:
    def inside(value, bounds):
        return bounds[0] <= value <= bounds[1]

    if inside(lat, CZ_LAT) and inside(lon, CZ_LON):
        return
    if inside(lon, CZ_LAT) and inside(lat, CZ_LON):
        raise QueryError(f"lat {lat} and lon {lon} look swapped: Czech latitudes are 48.5-51.1, longitudes 12-18.9.")
    raise QueryError(f"lat {lat}, lon {lon} is outside the Czech Republic (lat 48.5-51.1, lon 12-18.9).")


def origin_args(address_id, lat, lon) -> None:
    has_point = lat is not None or lon is not None
    if (address_id is None) == (not has_point) or (has_point and (lat is None or lon is None)):
        raise QueryError("Give either an address id, or both lat and lon (WGS84).")
    if has_point:
        check_cz_point(lat, lon)


def resolve_origin(conn, address_id, lat, lon) -> tuple[float, float, dict]:
    """(y, x, origin echo) of the origin: an address place or a WGS84 point."""
    origin_args(address_id, lat, lon)
    if address_id is None:
        y, x = wgs84_to_sjtsk(lat, lon)
        return y, x, {"lat": lat, "lon": lon}
    row = conn.execute(
        "SELECT souradnice_y::float8 AS y, souradnice_x::float8 AS x FROM adresni_misto WHERE kod = %(id)s",
        {"id": address_id},
    ).fetchone()
    if row is None:
        raise QueryError(f"Address place {address_id} does not exist.")
    if row["y"] is None or row["x"] is None:
        raise QueryError(f"Address place {address_id} has no coordinates.")
    return row["y"], row["x"], {"address_id": address_id}


def build_nearby(
    y: float, x: float, radius_m: int, *, exclude_id: int | None = None, filters: dict | None = None,
    limit: int = 20, offset: int = 0,
) -> tuple[str, dict, list[str]]:
    """Addresses within radius_m of (y, x), nearest first. A box on the SP-GiST index narrows the search."""
    if not 1 <= radius_m <= NEARBY_MAX_RADIUS_M:
        raise QueryError(f"radius_m must be between 1 and {NEARBY_MAX_RADIUS_M}.")
    _check_page(limit, offset)
    where, params = _where(NEARBY, filters=filters)
    params.update(cy=y, cx=x, y0=y - radius_m, x0=x - radius_m, y1=y + radius_m, x1=x + radius_m, radius=radius_m)
    where = [
        "t.poloha <@ box(point(%(y0)s, %(x0)s), point(%(y1)s, %(x1)s))",
        f"(t.poloha <-> {CENTER}) <= %(radius)s",
        *where,
    ]
    if exclude_id is not None:
        params["exclude_id"] = exclude_id
        where.append("t.kod <> %(exclude_id)s")
    params["limit"], params["offset"] = limit + 1, offset
    order = f"t.poloha <-> {CENTER}, t.kod"
    return _select(NEARBY, where, order, extra={"distance_m": DISTANCE}), params, where


def build_distances(y: float, x: float, ids: list[int]) -> tuple[str, dict]:
    if not ids:
        raise QueryError("to_address_ids must contain at least one address id.")
    if len(ids) > MAX_DISTANCE_TARGETS:
        raise QueryError(f"to_address_ids: at most {MAX_DISTANCE_TARGETS} ids, got {len(ids)}.")
    params = {"cy": y, "cx": x, "ids": ids, "limit": len(ids), "offset": 0}
    return _select(ADDRESS_PLACE, ["t.kod = ANY(%(ids)s)"], "t.kod", extra={"distance_m": DISTANCE}), params


def run_nearby(conninfo: str, args: dict) -> str:
    def work(conn):
        y, x, origin = resolve_origin(conn, args.get("address_id"), args.get("lat"), args.get("lon"))
        radius = args.get("radius_m") or NEARBY_DEFAULT_RADIUS_M
        filters = {key: args.get(key) for key in ("street_id_in", "municipality_id_in", "postal_code_in")}
        if filters["postal_code_in"]:
            filters["postal_code_in"] = [p.replace(" ", "") for p in filters["postal_code_in"]]
        sql, params, where = build_nearby(
            y, x, radius, exclude_id=args.get("address_id"), filters=filters,
            limit=args.get("limit", 20), offset=args.get("offset", 0),
        )
        result = _query_result(conn, NEARBY, sql, params, where, detail=bool(args.get("detail")))
        return {"origin": origin, "radius_m": radius, **result}

    return _db(conninfo, work)


def run_distances(conninfo: str, args: dict) -> str:
    def work(conn):
        y, x, origin = resolve_origin(conn, args.get("from_address_id"), args.get("lat"), args.get("lon"))
        ids = list(dict.fromkeys(args.get("to_address_ids") or []))  # unique, input order kept
        sql, params = build_distances(y, x, ids)
        rows = {row["id"]: row for row in conn.execute(sql, params).fetchall()}
        detail = bool(args.get("detail"))
        items = [address_item(rows[i], detail=detail) for i in ids if i in rows and rows[i]["distance_m"] is not None]
        return {
            "origin": origin,
            "items": items,
            "not_found": [i for i in ids if i not in rows],
            "without_coordinates": [i for i in ids if i in rows and rows[i]["distance_m"] is None],
            "distance": CROW_FLIES,
        }

    return _db(conninfo, work)


def nearby_args_schema():
    fields: dict = {
        "address_id": (int | None, Field(default=None, description="Origin address place id (from search_address).")),
        "lat": (float | None, Field(default=None, description="Origin latitude (WGS84), e.g. 50.0814. Use with lon instead of address_id.")),
        "lon": (float | None, Field(default=None, description="Origin longitude (WGS84), e.g. 14.4253.")),
        "radius_m": (int, Field(default=NEARBY_DEFAULT_RADIUS_M, ge=1, le=NEARBY_MAX_RADIUS_M,
                                description=f"Search radius in metres (1-{NEARBY_MAX_RADIUS_M}).")),
        "street_id_in": (list_of(int), Field(default=None, description=FILTER_HELP["street_id_in"])),
        "municipality_id_in": (list_of(int), Field(default=None, description=FILTER_HELP["municipality_id_in"])),
        "postal_code_in": (list_of(str), Field(default=None, description=FILTER_HELP["postal_code_in"])),
        "detail": (bool, Field(default=False, description=DETAIL_HELP)),
    }
    _page_fields(fields)
    return create_model("SearchNearbyArgs", **fields)


def distances_args_schema():
    fields: dict = {
        "from_address_id": (int | None, Field(default=None, description="Origin address place id (from search_address).")),
        "lat": (float | None, Field(default=None, description="Origin latitude (WGS84). Use with lon instead of from_address_id.")),
        "lon": (float | None, Field(default=None, description="Origin longitude (WGS84).")),
        "to_address_ids": (list_of(int), Field(description=f"Target address place ids, at most {MAX_DISTANCE_TARGETS}.")),
        "detail": (bool, Field(default=False, description=DETAIL_HELP)),
    }
    return create_model("AddressDistancesArgs", **fields)


def make_nearby_tool(conninfo: str) -> StructuredTool:
    def search(**args) -> str:
        return run_nearby(conninfo, args)

    return _tool("search_nearby", search, nearby_args_schema())


def make_distances_tool(conninfo: str) -> StructuredTool:
    def distances(**args) -> str:
        return run_distances(conninfo, args)

    return _tool("address_distances", distances, distances_args_schema(), result_help=DISTANCES_RESULT_HELP)


def validation_message(error) -> str:
    """Short text of a pydantic ValidationError, for example "limit: Input should be less than or equal to 200"."""
    errors = getattr(error, "errors", None)
    if not callable(errors):
        return f"Invalid arguments: {error}"
    parts = [f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}" for e in errors()]
    return "Invalid arguments: " + "; ".join(parts)


def _tool(name: str, func, schema, result_help: str = "") -> StructuredTool:
    description = TOOL_DESCRIPTIONS[name] + (result_help or RESULT_HELP)
    if name in ("search_address", "search_address_place", "search_nearby", "address_distances"):
        description += ADDRESS_ITEM_HELP
    return StructuredTool.from_function(
        func=func,
        name=name,
        description=description,
        args_schema=schema,
        # Bad arguments and query errors go back to the agent as the tool result, not as a failed run.
        handle_tool_error=True,
        handle_validation_error=validation_message,
    )


def make_tool(conninfo: str, entity: Entity) -> StructuredTool:
    def search(**args) -> str:
        return run_search(conninfo, entity, args)

    return _tool(entity.tool, search, args_schema(entity))


def make_address_tool(conninfo: str) -> StructuredTool:
    def search(**args) -> str:
        return run_address_search(conninfo, args)

    return _tool("search_address", search, address_args_schema())


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
        return [
            *(make_tool(conninfo, entity) for entity in ENTITIES.values()),
            make_address_tool(conninfo),
            make_nearby_tool(conninfo),
            make_distances_tool(conninfo),
        ]
