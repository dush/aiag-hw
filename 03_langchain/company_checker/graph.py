"""Plan-and-execute agent as a LangGraph graph.

    START -> planner -> executor -> replanner -+-> executor (more steps)
                                               +-> END      (final answer)

- planner: the model writes a short plan (structured output `Plan`).
- executor: a ReAct agent (`create_agent`) with the MCP tools runs ONE step.
- replanner: the model decides to answer or lists the remaining steps
  (structured output `Act`). The answer itself is a plain-text model call, so it
  keeps its line breaks and its length is not tied to a tool-call argument.
  A step cap guarantees the run ends.

Small models do better with one short step at a time than with one long
ReAct loop over the whole question.
"""

import logging
import operator
import os
from typing import Annotated, Any, Literal, TypedDict

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langchain_core.exceptions import OutputParserException
from pydantic import BaseModel, Field, ValidationError

from company_checker.prompts import (
    ANSWER_PROMPT,
    EXECUTOR_PROMPT,
    PLANNER_PROMPT,
    REPLANNER_PROMPT,
)

logger = logging.getLogger(__name__)

MAX_STEPS = 6
EXECUTOR_RECURSION_LIMIT = 12  # about 5 tool calls per step
STEP_RESULT_CHARS = 4000


class Plan(BaseModel):
    """Plan to answer the user's question."""

    steps: list[str] = Field(description="Ordered steps; each one short and doable with the tools")


class Act(BaseModel):
    """Next action of the replanner."""

    action: Literal["respond", "plan"] = Field(
        description="'respond' when the past steps answer the question, otherwise 'plan'"
    )
    steps: list[str] = Field(default_factory=list, description="Remaining steps (when action='plan')")


class StepRecord(TypedDict):
    step: str
    result: str
    tools: list[str]


class State(TypedDict, total=False):
    question: str
    plan: list[str]
    past_steps: Annotated[list[StepRecord], operator.add]
    response: str


def _format_steps(steps: list[str]) -> str:
    return "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)) or "(none)"


def _format_past(past: list[StepRecord]) -> str:
    if not past:
        return "(none)"
    return "\n\n".join(f"Step: {p['step']}\nResult: {p['result']}" for p in past)


async def _structured(runnable: Any, prompt: str) -> Any | None:
    """Structured output or None when the model did not produce valid JSON.

    With function calling, a model that answers in plain text yields None; a malformed
    tool call raises a parser or validation error. API errors (rate limit, ...) propagate.
    """
    try:
        return await runnable.ainvoke(prompt)
    except (OutputParserException, ValidationError) as exc:
        logger.warning("structured output failed: %s", exc)
        return None


def build_graph(model: BaseChatModel, tools: list[BaseTool], max_steps: int = MAX_STEPS):
    method = os.getenv("STRUCTURED_OUTPUT_METHOD", "function_calling")
    planner = model.with_structured_output(Plan, method=method)
    replanner = model.with_structured_output(Act, method=method)
    tool_list = "\n".join(f"- {t.name}: {(t.description or '').splitlines()[0]}" for t in tools)
    executor = create_agent(model, tools, system_prompt=EXECUTOR_PROMPT)

    async def plan_step(state: State) -> dict[str, Any]:
        plan = await _structured(
            planner, PLANNER_PROMPT.format(tools=tool_list, question=state["question"])
        )
        steps = plan.steps[:max_steps] if plan and plan.steps else [state["question"]]
        logger.info("plan:\n%s", _format_steps(steps))
        return {"plan": steps}

    async def execute_step(state: State) -> dict[str, Any]:
        step = state["plan"][0]
        task = (
            f"Question of the user: {state['question']}\n\n"
            f"Results of previous steps:\n{_format_past(state.get('past_steps', []))}\n\n"
            f"Your task now (do only this step): {step}"
        )
        logger.info("execute: %s", step)
        try:
            result = await executor.ainvoke(
                {"messages": [("user", task)]},
                config={"recursion_limit": EXECUTOR_RECURSION_LIMIT},
            )
            messages = result["messages"]
            used = [m.name for m in messages if isinstance(m, ToolMessage) and m.name]
            answer = next(
                (m.content for m in reversed(messages) if isinstance(m, AIMessage) and m.content),
                "",
            )
            text = answer if isinstance(answer, str) else str(answer)
        except Exception as exc:  # keep going; the replanner sees the failure
            logger.warning("step failed: %s", exc)
            used, text = [], f"Step failed: {type(exc).__name__}: {exc}"
        record: StepRecord = {"step": step, "result": text[:STEP_RESULT_CHARS], "tools": used}
        return {"past_steps": [record], "plan": state["plan"][1:]}

    async def replan_step(state: State) -> dict[str, Any]:
        past = state.get("past_steps", [])
        if len(past) >= max_steps:
            logger.info("step cap %d reached; answering", max_steps)
            return {"response": await _final_answer(state)}
        act = await _structured(
            replanner,
            REPLANNER_PROMPT.format(
                question=state["question"],
                plan=_format_steps(state.get("plan", [])),
                past=_format_past(past),
                tools=tool_list,
            ),
        )
        if act is None:  # no usable decision: go on with the old plan, or answer
            return {} if state.get("plan") else {"response": await _final_answer(state)}
        if act.action == "respond":
            return {"response": await _final_answer(state)}
        if act.steps:
            return {"plan": act.steps[: max_steps - len(past)]}
        if state.get("plan"):
            return {}
        return {"response": await _final_answer(state)}

    async def _final_answer(state: State) -> str:
        message = await model.ainvoke(
            ANSWER_PROMPT.format(question=state["question"],
                                 past=_format_past(state.get("past_steps", [])))
        )
        if message.response_metadata.get("finish_reason") == "length":
            logger.warning("final answer cut off by the output token limit")
        return message.content if isinstance(message.content, str) else str(message.content)

    def route(state: State) -> str:
        return END if state.get("response") else "executor"

    graph = StateGraph(State)
    graph.add_node("planner", plan_step)
    graph.add_node("executor", execute_step)
    graph.add_node("replanner", replan_step)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "replanner")
    graph.add_conditional_edges("replanner", route, ["executor", END])
    return graph.compile()
