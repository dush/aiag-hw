# frozen_string_literal: true

ENV["BUNDLE_GEMFILE"] ||= File.expand_path("Gemfile", __dir__)
require "bundler/setup"
require "dotenv"

# Repo-root .env, then the current directory's. Load before requiring weather_agent,
# which reads LLM_PROVIDER when it loads.
Dotenv.load(File.expand_path("../.env", __dir__), ".env")

require "logger"
require "ruby_llm"
require_relative "lib/weather_agent"

module App
  def self.config
    @config ||= {}
  end  

  def self.logger
    config[:logger]
  end
end

App.config[:logger] = Logger.new($stderr, level: ENV.fetch("LOG_LEVEL", "INFO"))

RubyLLM.configure do |config|
  config.openrouter_api_key = ENV.fetch("OPENROUTER_API_KEY", nil)
  config.openai_api_key = ENV.fetch("OPENAI_API_KEY", nil)
  config.anthropic_api_key = ENV.fetch("ANTHROPIC_API_KEY", nil)
  config.gemini_api_key = ENV.fetch("GEMINI_API_KEY", nil)
  config.logger = App.logger
end
 