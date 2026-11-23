"""Chat model behind the LiteLLM proxy (OpenAI-compatible API)."""

import os

from langchain_openai import ChatOpenAI

DEFAULT_BASE_URL = "http://127.0.0.1:4000/v1"  # LiteLLM from the repository root docker-compose.yml
DEFAULT_MODEL = "openrouter-gemma"
DEFAULT_API_KEY = "dummy-key"  # LiteLLM without a master key accepts any value


def chat_model(temperature: float = 0.0) -> ChatOpenAI:
    return ChatOpenAI(
        model=os.getenv("LLM_MODEL", DEFAULT_MODEL),
        base_url=os.getenv("LITELLM_BASE_URL", DEFAULT_BASE_URL),
        api_key=os.getenv("LITELLM_API_KEY", DEFAULT_API_KEY),
        temperature=temperature,
        timeout=120,
        max_retries=2,
    )
