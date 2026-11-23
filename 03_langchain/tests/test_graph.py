"""Offline test of the plan-and-execute graph with a scripted fake model."""

from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from company_checker.graph import build_graph


class ScriptedModel(GenericFakeChatModel):
    """Returns the scripted messages in order; accepts tools and structured output."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ScriptedModel":
        return self

    def with_structured_output(self, schema: Any, *, method: str | None = None, **kwargs: Any):
        return super().with_structured_output(schema, **kwargs)


def call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


calls: list[str] = []


@tool
def get_entity(ico: str) -> dict:
    """Basic data of a Czech company by IČO."""
    calls.append(ico)
    return {"ico": ico, "nazev": "Vzorová firma s.r.o.", "zanikla": False}


async def test_plan_execute_respond():
    calls.clear()
    model = ScriptedModel(messages=iter([
        call("Plan", {"steps": ["Načti data z ARES", "Shrň"]}, "p1"),       # planner
        call("get_entity", {"ico": "99999994"}, "t1"),                      # executor: tool call
        AIMessage(content="ARES: Vzorová firma s.r.o., aktivní."),            # executor: answer
        call("Act", {"action": "respond"}, "r1"),                             # replanner
        AIMessage(content="Vzorová firma s.r.o.\n- aktivní (ARES)"),          # final answer
    ]))
    graph = build_graph(model, [get_entity])
    result = await graph.ainvoke({"question": "Prověř IČO 99999994"})

    assert calls == ["99999994"]
    # The answer is a plain-text call, so its line breaks survive.
    assert result["response"] == "Vzorová firma s.r.o.\n- aktivní (ARES)"
    assert result["past_steps"][0]["tools"] == ["get_entity"]
    assert result["plan"] == ["Shrň"]


async def test_plain_text_from_replanner_falls_back_to_answer():
    # A weak model answers the replanner in plain text instead of calling the Act tool.
    model = ScriptedModel(messages=iter([
        call("Plan", {"steps": ["Načti data z ARES"]}, "p1"),
        AIMessage(content="ARES: Vzorová firma s.r.o."),   # executor
        AIMessage(content="Hotovo, firma je v pořádku."),  # replanner without tool call
        AIMessage(content="Souhrn z ARES."),               # final answer
    ]))
    result = await build_graph(model, [get_entity]).ainvoke({"question": "Prověř firmu"})

    assert result["response"] == "Souhrn z ARES."


async def test_plain_text_from_planner_uses_question_as_plan():
    model = ScriptedModel(messages=iter([
        AIMessage(content="Nejdřív ARES, pak shrnutí."),     # planner without tool call
        AIMessage(content="ARES: nic."),                     # executor
        call("Act", {"action": "respond"}, "r1"),
        AIMessage(content="Nic nenalezeno."),                # final answer
    ]))
    result = await build_graph(model, [get_entity]).ainvoke({"question": "Prověř firmu"})

    assert result["past_steps"][0]["step"] == "Prověř firmu"
    assert result["response"] == "Nic nenalezeno."


async def test_step_cap_forces_answer():
    # Replanner keeps asking for more steps; the cap ends the run with a summary.
    script = [call("Plan", {"steps": ["krok"]}, "p1")]
    for i in range(2):
        script.append(AIMessage(content=f"výsledek {i}"))
        script.append(call("Act", {"action": "plan", "steps": ["další krok"]}, f"r{i}"))
    script[-1] = AIMessage(content="Souhrn.")  # after 2 steps the cap triggers the final answer
    graph = build_graph(ScriptedModel(messages=iter(script)), [get_entity], max_steps=2)

    result = await graph.ainvoke({"question": "Prověř firmu"})

    assert len(result["past_steps"]) == 2
    assert result["response"] == "Souhrn."
