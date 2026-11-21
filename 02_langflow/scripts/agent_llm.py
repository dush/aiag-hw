"""Helpers for scripts/build_ruian_agent_flow.py: edges and the Agent's model.

configure_agent_model() sets the Agent's Language Model to the "OpenAI Compatible" model provider
(the lfx-openai-compatible bundle shipped with Langflow). The provider's base URL and API key are
Langflow global variables that docker-compose.yml imports from the environment:

    OPENAI_COMPATIBLE_BASE_URL  <- LLM_API_URL  (default http://litellm:4000/v1, the litellm service)
    OPENAI_COMPATIBLE_API_KEY   <- LLM_API_KEY  (default placeholder; LiteLLM accepts any key)

The model name is stored in the flow, so it is read here, at build time:

    LLM_MODEL  (process environment, default openrouter-gemma)
"""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROVIDER = "OpenAI Compatible"
DEFAULT_MODEL = "openrouter-gemma"


def handle(obj: dict) -> str:
    """Serialize a handle the way Langflow does (double quotes replaced by œ)."""
    return json.dumps(obj, separators=(",", ":")).replace('"', "œ")


def edge(src_id: str, src: dict, tgt_id: str, tgt: dict) -> dict:
    return {
        "animated": False,
        "className": "",
        "data": {"sourceHandle": src, "targetHandle": tgt},
        "id": f"xy-edge__{src_id}{handle(src)}-{tgt_id}{handle(tgt)}",
        "selected": False,
        "source": src_id,
        "sourceHandle": handle(src),
        "target": tgt_id,
        "targetHandle": handle(tgt),
    }


def model_name() -> str:
    return os.environ.get("LLM_MODEL", "").strip() or DEFAULT_MODEL


def model_option(name: str) -> dict:
    """A Language Model value in the format of Langflow's model picker (lfx unified model catalog)."""
    return {
        "name": name,
        "icon": "Plug",
        "category": PROVIDER,
        "provider": PROVIDER,
        "metadata": {
            "context_length": 128000,
            "model_class": "ChatOpenAI",
            "model_name_param": "model",
            "api_key_param": "api_key",
            "max_tokens_field_name": "max_tokens",
        },
    }


def configure_agent_model(agent: dict) -> str:
    """Point the Agent at the OpenAI Compatible provider and LLM_MODEL. Idempotent. Returns the model name."""
    name = model_name()
    template = agent["data"]["node"]["template"]
    template["model"]["value"] = [model_option(name)]
    # Empty: the provider resolves its key from the OPENAI_COMPATIBLE_API_KEY global variable.
    template["api_key"]["value"] = ""
    template["api_key"]["load_from_db"] = False
    return name
