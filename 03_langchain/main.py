"""Company checker CLI.

uv run python main.py "Prověř firmu s IČO 27082440"
uv run python main.py            # interactive
uv run python main.py -v "..."   # show the plan and each step
"""

import argparse
import asyncio
import logging

import openai
from dotenv import load_dotenv

from company_checker.run import ask, root_cause


async def answer(question: str, verbose: bool) -> None:
    result = None
    try:
        result = await ask(question)
    except* openai.APIError as group:  # rate limit, proxy down, wrong model name ...
        exc = root_cause(group)
        print(f"LLM error: {type(exc).__name__}: {str(exc)[:300]}")
    if result is None:
        return
    if verbose:
        for i, step in enumerate(result.steps, 1):
            tools = ", ".join(step["tools"]) or "no tool"
            print(f"\n--- step {i}: {step['step']} [{tools}]\n{step['result']}")
        print("\n=== answer ===")
    print(result.response)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check a Czech company (ARES, Wikipedia, web).")
    parser.add_argument("question", nargs="?", help="question; omit for interactive mode")
    parser.add_argument("-v", "--verbose", action="store_true", help="print the steps and logs")
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # MCP servers and HTTP clients log every request at INFO; keep them quiet.
    for noisy in ("httpx", "mcp", "openai", "fastmcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.question:
        asyncio.run(answer(args.question, args.verbose))
        return
    while True:
        try:
            question = input("\nOtázka (prázdná = konec): ").strip()
        except EOFError:
            break
        if not question:
            break
        asyncio.run(answer(question, args.verbose))


if __name__ == "__main__":
    main()
