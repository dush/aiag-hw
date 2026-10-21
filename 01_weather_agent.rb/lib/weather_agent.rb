# frozen_string_literal: true

require "ruby_llm"
require_relative "geocoder"
require_relative "my_location"
require_relative "weather_forecast"

# Talks to the model provider directly through RubyLLM (OpenRouter by default).
# Override with LLM_PROVIDER / LLM_MODEL; the provider's API key must be configured.
# ENV is read when this file loads, so load .env before requiring it.
class WeatherAgent < RubyLLM::Agent
  PROVIDER = ENV.fetch("LLM_PROVIDER", "openrouter").to_sym
  DEFAULT_MODEL = "google/gemma-4-31b-it:free" # free OpenRouter model
  OPENROUTER_FALLBACK = "nvidia/nemotron-3-ultra-550b-a55b:free"

  # LLM_MODEL may name any model the provider serves, including ones missing from
  # RubyLLM's registry, so skip the registry lookup.
  model(assume_model_exists: true, provider: PROVIDER) { ENV.fetch("LLM_MODEL", DEFAULT_MODEL) }

  instructions "You are a weather assistant. Use the geocoder tool to turn a place name " \
               "into coordinates, then the weather_forecast tool to get the forecast. " \
               "If the user does not name a place (e.g. 'here', 'my location'), use the " \
               "my_location tool to find the approximate current location first. " \
               "Answer concisely."

  # The free OpenRouter pool is often rate limited upstream; try another provider's
  # free model. Fallbacks must be in RubyLLM's registry and are OpenRouter-only.
  if PROVIDER == :openrouter
    fallbacks OPENROUTER_FALLBACK, on: [RubyLLM::RateLimitError, RubyLLM::ServerError]
  end

  tools Geocoder, MyLocation, WeatherForecast
end
