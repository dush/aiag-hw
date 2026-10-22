# Exercise 01: weather agent (Ruby / RubyLLM)

Ruby port of [01_weather_agent](../01_weather_agent) built on [RubyLLM](https://rubyllm.com/). It talks to the
model provider directly (OpenRouter by default); there is no LiteLLM proxy. RubyLLM runs the tool-calling loop.

- `weather`: executable shell script that runs `weather.rb` from any directory.
- `weather.rb`: entry point, calls `main`.
- `config.rb`: loads Bundler and `.env`, configures RubyLLM provider keys and logging, defines `main`.
- `lib/weather_agent.rb`: `WeatherAgent < RubyLLM::Agent` with the model, instructions, fallback and the three tools.
- `lib/geocoder.rb`: `Geocoder` tool, place name to coordinates (Open-Meteo geocoding).
- `lib/my_location.rb`: `MyLocation` tool, approximate location from the public IP ([ipwho.is](https://ipwho.is/)).
- `lib/weather_forecast.rb`: `WeatherForecast` tool, 7-day ECMWF forecast from Open-Meteo.
- `lib/base_tool.rb`: base class that returns tool exceptions to the model as `{ error: ... }` so it can recover.

```sh
cd 01_weather_agent.rb
bundle install
./weather
```

Configuration (env or `.env`): `OPENROUTER_API_KEY` (required for the default model) and optionally
`LLM_PROVIDER` (default `openrouter`) and `LLM_MODEL` (default `google/gemma-4-31b-it:free`) to use any other
provider RubyLLM supports, with its key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`).
`LOG_LEVEL` defaults to `INFO`.

The model must support tool calling. The free OpenRouter pool is often rate limited, so the agent falls back to
`nvidia/nemotron-3-ultra-550b-a55b:free` on rate-limit and server errors (only when the provider is OpenRouter).
Fallback models must be in RubyLLM's model registry. RubyLLM has no cap on tool-call rounds.
