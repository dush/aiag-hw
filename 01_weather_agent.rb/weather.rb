# frozen_string_literal: true

require_relative "config"
require_relative "lib/weather_agent"

agent = WeatherAgent.new
agent.before_tool_call { |call| App.logger.info("tool call: #{call.name} #{call.arguments}") }

print "Ask for a weather forecast (e.g. 'Weather in Prague' or 'Weather here'): "
puts agent.ask($stdin.gets.to_s.strip).content
