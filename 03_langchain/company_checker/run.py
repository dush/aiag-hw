"""Run one question through the agent with the MCP tools open."""

from dataclasses import dataclass, field

from company_checker.graph import MAX_STEPS, StepRecord, build_graph
from company_checker.llm import chat_model
from company_checker.mcp_servers import open_tools


def root_cause(exc: BaseException) -> BaseException:
    """First leaf error. MCP sessions run in task groups, so errors arrive wrapped
    in (nested) ExceptionGroups."""
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


@dataclass
class Answer:
    question: str
    response: str
    steps: list[StepRecord] = field(default_factory=list)

    @property
    def tools_used(self) -> list[str]:
        return [name for step in self.steps for name in step["tools"]]


async def ask(question: str, max_steps: int = MAX_STEPS) -> Answer:
    async with open_tools() as tools:
        graph = build_graph(chat_model(), tools, max_steps=max_steps)
        # Outer limit: planner + (executor + replanner) per step, with a margin.
        state = await graph.ainvoke({"question": question},
                                    config={"recursion_limit": 3 * max_steps + 5})
    return Answer(question=question, response=state.get("response", ""),
                  steps=state.get("past_steps", []))
