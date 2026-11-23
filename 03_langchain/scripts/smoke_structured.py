"""Check which structured-output method works with the configured model.

uv run python -m scripts.smoke_structured
"""

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from company_checker.llm import chat_model


class Plan(BaseModel):
    steps: list[str] = Field(description="Ordered steps")


PROMPT = "Make a 3-step plan to check the Czech company with IČO 27082440 (registry, news, summary)."

if __name__ == "__main__":
    load_dotenv()
    for method in ("json_schema", "function_calling", "json_mode"):
        try:
            result = chat_model().with_structured_output(Plan, method=method).invoke(
                PROMPT if method != "json_mode" else PROMPT + ' Reply as JSON {"steps": [...]}.'
            )
            print(f"{method}: OK {result!r}")
        except Exception as exc:
            print(f"{method}: FAIL {type(exc).__name__}: {str(exc)[:300]}")
