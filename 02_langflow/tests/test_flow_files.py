"""Static checks of the committed flow JSON (no Langflow server needed).

Langflow's UI drops an edge whose handle does not match the target field (and then saves the flow),
which once disconnected the Agent from Chat Output. Langflow also refuses tool calls unless the tool
node is in tool mode.
"""

import json
from pathlib import Path

import pytest

FLOWS = sorted((Path(__file__).resolve().parent.parent / "flows").glob("*.json"))


@pytest.fixture(params=FLOWS, ids=[p.name for p in FLOWS])
def flow(request):
    return json.loads(request.param.read_text(encoding="utf-8"))


def test_edge_handles_match_node_templates(flow):
    nodes = {n["id"]: n["data"]["node"] for n in flow["data"]["nodes"]}
    for e in flow["data"]["edges"]:
        src, tgt = e["data"]["sourceHandle"], e["data"]["targetHandle"]
        output = next(o for o in nodes[e["source"]]["outputs"] if o["name"] == src["name"])
        field = nodes[e["target"]]["template"][tgt["fieldName"]]
        where = f"{e['source']}.{src['name']} -> {e['target']}.{tgt['fieldName']}"
        assert src["output_types"] == output["types"], where
        assert sorted(tgt["inputTypes"]) == sorted(field.get("input_types") or []), where
        assert tgt["type"] == field["type"], where
        assert set(src["output_types"]) & set(tgt["inputTypes"]), where


def test_tool_nodes_are_in_tool_mode(flow):
    for n in flow["data"]["nodes"]:
        node = n["data"]["node"]
        if [o["name"] for o in node.get("outputs", [])] == ["component_as_tool"]:
            assert node.get("tool_mode") is True, n["id"]


def test_agent_reaches_chat_output(flow):
    types = {n["id"]: n["data"]["type"] for n in flow["data"]["nodes"]}
    if "Agent" in types.values() and "ChatOutput" in types.values():
        assert any(types[e["source"]] == "Agent" and types[e["target"]] == "ChatOutput" for e in flow["data"]["edges"])
