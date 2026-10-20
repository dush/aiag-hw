# Exercise 01: weather agent

An `Agent` ([agent.py](agent.py)) with three tools:

- `Geocoder`: place name to coordinates (Open-Meteo geocoding).
- `MyLocation`: approximate current location of this machine, looked up from its public IP address
  on [ipwho.is](https://ipwho.is/).
- `WeatherForecast`: 7-day ECMWF forecast from Open-Meteo.

None of the APIs needs a key.

```sh
cd 01_weather_agent
uv sync
uv run python main.py
```

Enter a question when prompted, e.g. `What's the weather forecast in Prague?`, or `What's the weather here?`
to have the agent find your location from your IP address. The agent calls the tools and prints the final answer.

`MyLocation` is city-level at best and wrong behind a VPN. It sends your public IP to ipwho.is.

Files:

- `agent.py`: `Agent(model, api_key, base_url, instructions, tools)` with `ask(prompt)` returning an `AgentResponse`
  (`content`, `tool_calls`, `usage`, `messages`). It defaults to the local LiteLLM proxy and `openrouter-gemma`.
- `tool.py`: `Tool` wraps a function or callable object and builds its JSON schema.
- `geocoder.py`, `my_location.py`, `weather_forecast.py`: the three tools.
- `main.py`: the interactive entry point.

Configuration (env or `.env`, all optional): `LITELLM_BASE_URL` (default `http://0.0.0.0:4000`) and
`LITELLM_API_KEY` (default `dummy-key`, which the proxy accepts in its local-dev config).

## Troubleshooting

- `Connection refused`: the proxy isn't running. Run `docker compose up -d`.
- `429 ... rate-limited upstream`: the free pool is busy. Retry in a minute; the proxy already retries and falls back.
- `docker compose logs litellm` shows why the proxy failed to start.
