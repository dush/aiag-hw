# frozen_string_literal: true

require_relative "http_utils"
require_relative "weather_tool"

Location = Data.define(:name, :latitude, :longitude)

# Resolves a place name to coordinates via Open-Meteo geocoding.
class Geocoder < WeatherTool
  GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

  description "Resolves a place name to a Location (name, latitude, longitude) via Open-Meteo geocoding."
  parameter :name, description: "Place name, e.g. 'Prague'"

  def initialize(timeout: 10.0)
    super()
    @timeout = timeout
  end

  def execute(name:)
    data = HttpUtils.get_json(GEOCODING_URL, { name: name, count: 1 }, @timeout)
    best = data["results"]&.first
    fail LookupError, "location not found: #{name.inspect}" unless best

    label = [best["name"], best["country"]].compact.join(", ")
    Location.new(label, best["latitude"], best["longitude"]).to_h
  end

  class LookupError < StandardError; end
end
