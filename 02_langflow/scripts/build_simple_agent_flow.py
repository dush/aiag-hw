"""Update flows/simple-agent.json: set its Agent's model to the "OpenAI Compatible" provider.

Model: LLM_MODEL from the environment (default openrouter/free). Base URL and API key come from
Langflow global variables at runtime (see scripts/agent_llm.py). The other nodes and the flow id stay
as they are. Safe to run again.

Usage: [LLM_MODEL=...] uv run python scripts/build_simple_agent_flow.py
"""

import json

from agent_llm import ROOT, configure_agent_model

FLOW = ROOT / "flows" / "simple-agent.json"


def main() -> None:
    flow = json.loads(FLOW.read_text(encoding="utf-8"))
    agent = next(n for n in flow["data"]["nodes"] if n["data"]["type"] == "Agent")
    model = configure_agent_model(agent)
    flow["description"] = f"Chat Input -> Agent (OpenAI Compatible provider, model {model}) -> Chat Output"
    FLOW.write_text(json.dumps(flow, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {FLOW} (model {model})")


if __name__ == "__main__":
    main()
