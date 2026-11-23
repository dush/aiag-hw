"""Run the evaluation questions against the live agent (needs the LLM and ARES).

uv run python -m scripts.eval_agent [question-id ...]

A question passes when the answer contains every `expect_all` string, at least one
`expect_any` string (if given), and the agent called every tool in `expect_tools`.
Matching ignores case.
"""

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from company_checker.run import ask, root_cause

QUESTIONS = Path(__file__).resolve().parent.parent / "tests" / "eval_questions.json"


def check(case: dict, response: str, tools: list[str]) -> list[str]:
    text = response.lower()
    problems = [f"missing {s!r}" for s in case.get("expect_all", []) if s.lower() not in text]
    if (any_of := case.get("expect_any")) and not any(s.lower() in text for s in any_of):
        problems.append(f"none of {any_of}")
    problems += [f"tool {t} not called" for t in case.get("expect_tools", []) if t not in tools]
    return problems


async def main(ids: list[str]) -> int:
    cases = [c for c in json.loads(QUESTIONS.read_text()) if not ids or c["id"] in ids]
    failed = 0
    for case in cases:
        try:
            answer = await ask(case["question"])
            problems = check(case, answer.response, answer.tools_used)
            tools = answer.tools_used
        except Exception as exc:
            cause = root_cause(exc)
            problems, tools = [f"error {type(cause).__name__}: {str(cause)[:300]}"], []
        failed += bool(problems)
        print(f"{'PASS' if not problems else 'FAIL'} {case['id']} tools={tools}")
        for problem in problems:
            print(f"     - {problem}")
    print(f"\n{len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    load_dotenv()
    sys.exit(asyncio.run(main(sys.argv[1:])))
