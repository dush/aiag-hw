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

## 2. Exercise: l1 (weather agent)

An `Agent` ([l1/agent.py](l1/agent.py)) with two tools, `Geocoder` (place name to coordinates) and
`WeatherForecast` (7-day ECMWF forecast from Open-Meteo). Neither Open-Meteo API needs a key.

```sh
cd 01_weather_agent
uv sync
uv run python main.py
```

Enter a question when prompted, e.g. `What's the weather forecast in Prague?`. The agent calls the tools
and prints the final answer.

Files:

- `agent.py`: `Agent(model, api_key, base_url, instructions, tools)` with `ask(prompt)` returning an `AgentResponse`
  (`content`, `tool_calls`, `usage`, `messages`). It defaults to the local LiteLLM proxy and `openrouter-gemma`.
- `tool.py`: `Tool` wraps a function or callable object and builds its JSON schema.
- `geocoder.py`, `weather_forecast.py`: the two tools.
- `main.py`: the interactive entry point.

Configuration (env or `.env`, all optional): `LITELLM_BASE_URL` (default `http://0.0.0.0:4000`) and
`LITELLM_API_KEY` (default `dummy-key`, which the proxy accepts in its local-dev config).

## Troubleshooting

- `Connection refused`: the proxy isn't running. Run `docker compose up -d`.
- `429 ... rate-limited upstream`: the free pool is busy. Retry in a minute; the proxy already retries and falls back.
- `docker compose logs litellm` shows why the proxy failed to start.
