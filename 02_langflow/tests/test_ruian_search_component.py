"""Unit tests for the RUIAN Search component: SQL builders and tool schemas (no database needed)."""

import pytest

from components import ruian_search_component as q


def test_name_match_uses_fulltext_and_relevance_by_default():
    sql, params = q.build_search(q.STREET, name_match="nadraz br")
    assert "t.nazev_tsv @@ to_tsquery('simple'" in sql
    order = sql.split(" ORDER BY ")[1]
    assert order.startswith("(ruian_norm(t.nazev) = ")
    assert "ts_rank(" in order
    assert order.endswith("t.kod LIMIT %(limit)s OFFSET %(offset)s")
    assert params["name_match"] == "nadraz br"


def test_user_text_is_never_in_sql():
    evil = "x'); DROP TABLE obec; --"
    sql, params = q.build_search(q.MUNICIPALITY, name_match=evil)
    assert evil not in sql
    assert params["name_match"] == evil


def test_name_match_without_letters_is_rejected():
    with pytest.raises(q.QueryError, match="letter or digit"):
        q.build_search(q.MUNICIPALITY, name_match=" - ")


def test_limit_fetches_one_extra_row_for_has_more():
    _, params = q.build_search(q.MUNICIPALITY, limit=5, offset=10)
    assert params["limit"] == 6
    assert params["offset"] == 10


@pytest.mark.parametrize("limit", [0, q.MAX_LIMIT + 1])
def test_limit_bounds(limit):
    with pytest.raises(q.QueryError, match="limit"):
        q.build_search(q.MUNICIPALITY, limit=limit)


def test_id_in_only_keeps_input_order():
    sql, params = q.build_search(q.STREET, id_in=[3, 1, 2])
    assert "t.kod = ANY(%(id_in)s)" in sql
    assert "ORDER BY array_position(%(id_in)s::int[], t.kod)" in sql
    assert params["id_in"] == [3, 1, 2]


def test_default_sort_without_filters_is_czech_name_then_id():
    sql, _ = q.build_search(q.MUNICIPALITY)
    assert 'ORDER BY t.nazev COLLATE "cs-CZ-x-icu", t.kod LIMIT' in sql
    assert " WHERE " not in sql


def test_explicit_sort_with_descending_and_tiebreak():
    sql, _ = q.build_search(q.STREET, sort=["-municipality_id", "name"])
    assert 'ORDER BY t.obec_kod DESC, t.nazev COLLATE "cs-CZ-x-icu", t.kod LIMIT' in sql


def test_parent_filter():
    sql, params = q.build_search(q.STREET, filters={"municipality_id_in": [582786]})
    assert "t.obec_kod = ANY(%(municipality_id_in)s)" in sql
    assert params["municipality_id_in"] == [582786]


def test_unknown_filter_is_rejected():
    with pytest.raises(q.QueryError, match="no filter"):
        q.build_search(q.MUNICIPALITY, filters={"street_id_in": [1]})


def test_relevance_needs_name_match():
    with pytest.raises(q.QueryError, match="needs name_match"):
        q.build_search(q.STREET, sort=["relevance"])


def test_unknown_sort_key_lists_allowed_keys():
    with pytest.raises(q.QueryError, match="Allowed: relevance, name, id, municipality_id"):
        q.build_search(q.STREET, sort=["nazev"])


def test_too_many_ids():
    with pytest.raises(q.QueryError, match="at most"):
        q.build_search(q.STREET, id_in=list(range(q.MAX_IDS + 1)))


def test_address_place_requires_a_filter():
    with pytest.raises(q.QueryError, match="at least one filter"):
        q.build_search(q.ADDRESS_PLACE)


def test_address_place_has_no_name_match():
    with pytest.raises(q.QueryError, match="does not support name_match"):
        q.build_search(q.ADDRESS_PLACE, name_match="x", filters={"street_id_in": [1]})


def test_address_place_default_sort_and_scalar_filter():
    sql, params = q.build_search(
        q.ADDRESS_PLACE, filters={"street_id_in": [1]}, scalars={"building_number": 681, "orientation_number": None}
    )
    assert "t.cislo_domovni = %(building_number)s" in sql
    assert "orientation_number" not in params
    order = sql.split(" ORDER BY ")[1]
    assert order.startswith('coalesce(u.nazev, co.nazev) COLLATE "cs-CZ-x-icu", t.cislo_orientacni, ')
    assert "t.cislo_domovni, t.kod LIMIT" in order


def _addr(**kw):
    row = {
        "building_number_type": "descriptive", "building_number": 681, "orientation_number": 36,
        "orientation_number_letter": None, "postal_code": "11000", "street_name": "Vodičkova",
        "municipality_part_name": "Nové Město", "prague_district_name": "Praha 1", "municipality_name": "Praha",
    }
    row.update(kw)
    return row


def test_format_address_prague():
    assert q.format_address(_addr()) == "Vodičkova 681/36, Nové Město, 110 00 Praha 1"


def test_format_address_without_street_and_same_part_name():
    row = _addr(street_name=None, orientation_number=None, municipality_part_name="Lhota",
                prague_district_name=None, municipality_name="Lhota", postal_code="51101")
    assert q.format_address(row) == "Lhota 681, 511 01 Lhota"


def test_format_address_registration_number_and_letter():
    row = _addr(building_number_type="registration", orientation_number_letter="a",
                municipality_part_name="Brno-město", municipality_name="Brno", prague_district_name=None,
                postal_code="60200")
    assert q.format_address(row) == "Vodičkova č. ev. 681/36a, Brno-město, 602 00 Brno"


def test_postal_code_filter_casts_to_char_for_the_index():
    sql, _ = q.build_search(q.ADDRESS_PLACE, filters={"postal_code_in": ["11000"]})
    assert "t.psc = ANY(%(postal_code_in)s::bpchar[])" in sql


# -- tools (args schemas, error handling); no database connection is opened ----------------------

CONNINFO = "host=invalid dbname=ruian"


def _tool(name):
    return q.make_tool(CONNINFO, q.ENTITIES[name])


def test_one_tool_per_entity_with_the_old_names():
    assert list(q.ENTITIES) == [
        "search_municipality", "search_prague_district", "search_city_district",
        "search_municipality_part", "search_street", "search_address_place",
    ]


def test_args_schema_fields():
    props = _tool("search_address_place").args_schema.model_json_schema()["properties"]
    assert "name_match" not in props
    assert {"street_id_in", "postal_code_in", "building_number", "orientation_number", "sort", "limit"} <= set(props)
    sort_enum = props["sort"]["anyOf"][0]["items"]["enum"]
    assert "-postal_code" in sort_enum
    assert "relevance" not in sort_enum


def test_argument_errors_are_returned_to_the_agent():
    assert _tool("search_street").invoke({"sort": ["relevance"]}) == "Sort by relevance needs name_match."
    assert "at least one filter" in _tool("search_address_place").invoke({})
    out = _tool("search_municipality").invoke({"limit": 500})
    assert out.startswith("Invalid arguments: limit:")


def test_single_values_are_accepted_for_list_arguments():
    model = _tool("search_address_place").args_schema
    args = model.model_validate({"street_id_in": 478156, "postal_code_in": "11000", "sort": "-id"})
    assert args.street_id_in == [478156]
    assert args.postal_code_in == ["11000"]
    assert args.sort == ["-id"]
    props = model.model_json_schema()["properties"]
    assert props["street_id_in"]["anyOf"][0]["type"] == "array"


def test_postal_codes_lose_spaces():
    kwargs = q.search_kwargs(q.ADDRESS_PLACE, {"postal_code_in": ["110 00"]})
    assert kwargs["filters"]["postal_code_in"] == ["11000"]


def test_component_loads_from_code_string(monkeypatch):
    """Langflow runs the embedded code string, not an import (postponed annotations etc.)."""
    import asyncio
    from pathlib import Path

    pytest.importorskip("psycopg")
    from lfx.custom.eval import eval_custom_component_code

    monkeypatch.setenv("RUIAN_DATABASE_URL", "postgresql://u:p@db:5432/ruian")
    code = Path(q.__file__).read_text(encoding="utf-8")
    tools = asyncio.run(eval_custom_component_code(code)().to_toolkit())
    assert [t.name for t in tools] == list(q.ENTITIES)
    assert tools[0].invoke({"limit": 0}).startswith("Invalid arguments: limit:")
