# AI Agents HomeWorks

## Prerequisites

- Docker with Compose
- [uv](https://docs.astral.sh/uv/) (Python 3.14, see `l1/.python-version`)
- An [OpenRouter](https://openrouter.ai/keys) API key

## 1. Run LiteLLM

The proxy exposes an OpenAI-compatible API on `http://127.0.0.1:4000` and routes to free OpenRouter models
(see [litellm_config.yaml](litellm_config.yaml)).

```sh
cp .env.example .env        # then set OPENROUTER_API_KEY in .env
docker compose up -d
curl http://127.0.0.1:4000/health/liveliness    # -> "I'm alive!"
```

Models served:

| Alias | OpenRouter model | Role |
|---|---|---|
| `openrouter-gemma` | `google/gemma-4-31b-it:free` | default |
| `openrouter-minimax` | `minimax/minimax-m3.1-flash:free` | fallback on 429 |

Useful commands:

```sh
docker compose logs -f litellm   # watch requests
docker compose restart litellm   # after editing litellm_config.yaml
docker compose down              # stop
```

Free models are rate limited, so a 429 now and then is expected. If a model id stops working, pick another `:free`
id from <https://openrouter.ai/models?q=free> and update the config.

