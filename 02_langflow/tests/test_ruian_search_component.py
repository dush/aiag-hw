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
    # search_address is an extra tool (make_address_tool), not an entity.
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
    assert [t.name for t in tools] == [*q.ENTITIES, "search_address", "search_nearby", "address_distances"]
    assert tools[0].invoke({"limit": 0}).startswith("Invalid arguments: limit:")


# -- total, name_mode, search_address, compact items, GPS --------------------------------------


def test_count_query_uses_base_table_and_cap():
    where, params = q._where(q.STREET, name_match="nadraz", filters={"municipality_id_in": [582786]})
    sql = q.build_count(q.STREET, where)
    assert sql.startswith("SELECT count(*) AS n FROM (SELECT 1 FROM ulice t WHERE ")
    assert " JOIN " not in sql
    assert sql.endswith(f"LIMIT {q.STREET.count_cap + 1}) s")
    assert q.ADDRESS_PLACE.count_cap == 10_000


@pytest.mark.parametrize(
    ("mode", "extra"),
    [("word_prefix", None), ("starts_with", "LIKE"), ("exact", ") = ruian_norm(btrim(%(name_match)s))")],
)
def test_name_mode_adds_condition_and_keeps_fulltext(mode, extra):
    sql, _ = q.build_search(q.STREET, name_match="Nádraž", name_mode=mode)
    where = sql.split(" FROM ", 1)[1].split(" ORDER BY ")[0]
    assert "t.nazev_tsv @@ to_tsquery" in where
    if extra:
        assert extra in where
    else:
        assert "LIKE" not in where


def test_unknown_name_mode_is_rejected():
    with pytest.raises(q.QueryError, match="name_mode"):
        q.build_search(q.STREET, name_match="x", name_mode="fuzzy")


@pytest.mark.parametrize("value", ["null", "None", " ", ""])
def test_null_words_count_as_not_set(value):
    kwargs = q.search_kwargs(q.STREET, {"name_match": value, "municipality_id_in": [554782]})
    assert kwargs["name_match"] is None
    args = _tool("search_street").args_schema.model_validate({"name_match": value})
    assert args.name_match is None


def test_address_terms_split_like_the_index():
    assert q.address_terms("Vodičkova 704/36, Praha 1") == ["vodickova", "704", "36", "praha", "1"]
    assert q.address_terms("Bukovina č. p. 5, PSČ 679 05") == ["bukovina", "5", "67905"]
    assert q.address_terms("ulice Nádražní 16a") == ["nadrazni", "16a"]


def test_address_search_sql():
    sql, params, where = q.build_address_search("Vodičkova 36 Praha", municipality_id_in=[554782], limit=5)
    assert params["address_query"] == "'vodickova':* & '36' & 'praha':*"
    assert params["numbers"] == [36]
    assert where == ["t.adresa_tsv @@ to_tsquery('simple', %(address_query)s)", "t.obec_kod = ANY(%(municipality_id_in)s)"]
    order = sql.split(" ORDER BY ")[1]
    assert order.startswith("coalesce(t.cislo_orientacni = ANY(%(numbers)s::int[]), false) DESC")
    assert params["limit"] == 6
    assert "Vodičkova" not in sql


def test_address_search_needs_terms():
    with pytest.raises(q.QueryError, match="query must contain"):
        q.build_address_search("č. p. ulice")


def test_address_tool_schema():
    tool = q.make_address_tool(CONNINFO)
    props = tool.args_schema.model_json_schema()["properties"]
    assert set(props) == {"query", "municipality_id_in", "detail", "limit", "offset"}
    assert "Invalid arguments" in tool.invoke({"query": "x", "limit": 0})


def _row():
    return {
        "id": 21704970, "building_number_type": "descriptive", "building_number": 704, "orientation_number": 36,
        "orientation_number_letter": None, "postal_code": "11000", "street_id": 478156, "street_name": "Vodičkova",
        "municipality_part_id": 490148, "municipality_part_name": "Nové Město", "city_district_id": 500054,
        "city_district_name": "Praha 1", "prague_district_id": 19, "prague_district_name": "Praha 1",
        "municipality_id": 554782, "municipality_name": "Praha", "sjtsk_x": 1043719.26, "sjtsk_y": 742614.93,
        "valid_from": "2017-07-04",
    }


def test_compact_address_item():
    item = q.address_item(_row(), detail=False)
    assert item["formatted"] == "Vodičkova 704/36, Nové Město, 110 00 Praha 1"
    assert "orientation_number_letter" not in item  # null fields are left out
    assert "street_name" not in item and "sjtsk_x" not in item
    assert {"lat", "lon", "street_id", "municipality_id"} <= set(item)
    full = q.address_item(_row(), detail=True)
    assert {"street_name", "sjtsk_x", "valid_from", "lat", "formatted"} <= set(full)


# Reference values: pyproj, EPSG:5514 (x=-souradnice_y, y=-souradnice_x) -> EPSG:4326.
GPS_REFERENCE = [
    (742614.93, 1043719.26, 50.081412, 14.425295),  # Vodičkova 704/36, Praha
    (598212.69, 1160715.55, 49.195390, 16.607256),  # náměstí Svobody 95/1, Brno
]


@pytest.mark.parametrize(("y", "x", "lat", "lon"), GPS_REFERENCE)
def test_sjtsk_to_wgs84_reference_points(y, x, lat, lon):
    got_lat, got_lon = q.sjtsk_to_wgs84(y, x)
    # 0.00003 deg is about 3 m (latitude) / 2 m (longitude).
    assert abs(got_lat - lat) < 3e-5 and abs(got_lon - lon) < 3e-5, (got_lat, got_lon)


def test_sjtsk_to_wgs84_matches_pyproj_across_the_country():
    pyproj = pytest.importorskip("pyproj")
    to_wgs = pyproj.Transformer.from_crs("EPSG:5514", "EPSG:4326", always_xy=True)
    # Corners and centre of the Czech Republic in S-JTSK (y, x; positive).
    for y, x in [(905000, 1230000), (431000, 935000), (700000, 1100000), (480000, 1250000), (850000, 960000)]:
        lon, lat = to_wgs.transform(-y, -x)
        got_lat, got_lon = q.sjtsk_to_wgs84(y, x)
        assert abs(got_lat - lat) < 5e-5 and abs(got_lon - lon) < 5e-5, (y, x, got_lat - lat, got_lon - lon)


# -- distances: wgs84_to_sjtsk, search_nearby, address_distances -------------------------------


@pytest.mark.parametrize(("y", "x", "lat", "lon"), GPS_REFERENCE)
def test_wgs84_to_sjtsk_round_trip(y, x, lat, lon):
    back_y, back_x = q.wgs84_to_sjtsk(*q.sjtsk_to_wgs84(y, x))
    assert abs(back_y - y) < 1e-3 and abs(back_x - x) < 1e-3  # 1 mm
    got_y, got_x = q.wgs84_to_sjtsk(lat, lon)  # pyproj reference point -> within a few metres
    assert abs(got_y - y) < 3 and abs(got_x - x) < 3, (got_y - y, got_x - x)


def test_wgs84_to_sjtsk_matches_pyproj():
    pyproj = pytest.importorskip("pyproj")
    to_sjtsk = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:5514", always_xy=True)
    for lat, lon in [(50.08, 14.42), (49.19, 16.61), (50.77, 15.06), (48.85, 17.12), (50.2, 12.9)]:
        e, n = to_sjtsk.transform(lon, lat)  # EPSG:5514 axes: (-y, -x)
        y, x = q.wgs84_to_sjtsk(lat, lon)
        assert abs(y + e) < 5 and abs(x + n) < 5, (lat, lon, y + e, x + n)


def test_sjtsk_distance_against_geodesic():
    """S-JTSK is a conformal projection for Czechia: Euclidean distance is close to the WGS84 geodesic."""
    pyproj = pytest.importorskip("pyproj")
    geod = pyproj.Geod(ellps="WGS84")
    pairs = [
        ((742614.93, 1043719.26), (598212.69, 1160715.55)),  # Praha Vodičkova 36 -> Brno nám. Svobody 1, ~186 km
        ((742614.93, 1043719.26), (742595.0, 1043730.0)),  # ~23 m
        ((742614.93, 1043719.26), (742100.0, 1044250.0)),  # ~740 m
    ]
    for (y1, x1), (y2, x2) in pairs:
        euclid = ((y1 - y2) ** 2 + (x1 - x2) ** 2) ** 0.5
        (lat1, lon1), (lat2, lon2) = q.sjtsk_to_wgs84(y1, x1), q.sjtsk_to_wgs84(y2, x2)
        _, _, geodesic = geod.inv(lon1, lat1, lon2, lat2)
        assert abs(euclid - geodesic) / geodesic < 2e-4, (euclid, geodesic)  # < 0.02 %


@pytest.mark.parametrize(
    ("lat", "lon", "message"),
    [(14.42, 50.08, "look swapped"), (48.1, 16.4, "outside the Czech Republic"), (52.5, 13.4, "outside")],
)
def test_check_cz_point(lat, lon, message):
    with pytest.raises(q.QueryError, match=message):
        q.check_cz_point(lat, lon)


@pytest.mark.parametrize(
    "args",
    [{}, {"address_id": 1, "lat": 50.0, "lon": 14.0}, {"lat": 50.0}, {"lon": 14.0}],
)
def test_origin_needs_exactly_one_form(args):
    with pytest.raises(q.QueryError, match="either an address id"):
        q.origin_args(args.get("address_id"), args.get("lat"), args.get("lon"))


def test_nearby_sql_uses_y_x_order_and_box():
    sql, params, where = q.build_nearby(742614.93, 1043719.26, 500, exclude_id=21704970,
                                        filters={"street_id_in": [478156]}, limit=5)
    assert (params["cy"], params["cx"]) == (742614.93, 1043719.26)
    assert (params["y0"], params["x0"], params["y1"], params["x1"]) == (742114.93, 1043219.26, 743114.93, 1044219.26)
    assert where[0] == "t.poloha <@ box(point(%(y0)s, %(x0)s), point(%(y1)s, %(x1)s))"
    assert where[1] == "(t.poloha <-> point(%(cy)s, %(cx)s)) <= %(radius)s"
    assert "t.ulice_kod = ANY(%(street_id_in)s)" in where and "t.kod <> %(exclude_id)s" in where
    assert '(round(t.poloha <-> point(%(cy)s, %(cx)s)))::int AS "distance_m"' in sql
    assert sql.split(" ORDER BY ")[1].startswith("t.poloha <-> point(%(cy)s, %(cx)s), t.kod LIMIT")
    assert q.build_count(q.NEARBY, where).startswith("SELECT count(*) AS n FROM (SELECT 1 FROM adresni_misto t WHERE ")


def test_nearby_radius_limits():
    with pytest.raises(q.QueryError, match="radius_m"):
        q.build_nearby(700000, 1100000, q.NEARBY_MAX_RADIUS_M + 1)


def test_distances_sql_and_limits():
    sql, params = q.build_distances(742614.93, 1043719.26, [3, 1, 2])
    assert params["ids"] == [3, 1, 2] and "t.kod = ANY(%(ids)s)" in sql and '"distance_m"' in sql
    with pytest.raises(q.QueryError, match="at most 100"):
        q.build_distances(0, 0, list(range(101)))
    with pytest.raises(q.QueryError, match="at least one"):
        q.build_distances(0, 0, [])


def test_distance_tools_schemas_and_crow_flies_note():
    nearby = q.make_nearby_tool(CONNINFO)
    distances = q.make_distances_tool(CONNINFO)
    assert "as the crow flies" in nearby.description and "NOT the distance by road" in nearby.description
    assert "as the crow flies" in distances.description and "NOT the distance by road" in distances.description
    props = distances.args_schema.model_json_schema()["properties"]
    assert {"from_address_id", "lat", "lon", "to_address_ids"} <= set(props)
    # Argument errors come back to the agent before any database connection is opened.
    assert "radius_m" in nearby.invoke({"address_id": 1, "radius_m": 50000})
    assert distances.args_schema.model_validate({"from_address_id": 1, "to_address_ids": 5}).to_address_ids == [5]
