# frozen_string_literal: true

require "httpx"
require_relative "base_tool"

Location = Data.define(:name, :latitude, :longitude)

# Resolves a place name to coordinates via Open-Meteo geocoding.
class Geocoder < BaseTool
  GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"

  description "Resolves a place name to a Location (name, latitude, longitude) via Open-Meteo geocoding."
  parameter :name, description: "Place name, e.g. 'Prague'"

  def initialize(timeout: 10.0)
    super()
    @http = HTTPX.with(timeout: { request_timeout: timeout })
  end

  def execute(name:)
    data = @http.get(GEOCODING_URL, params: { name: name, count: 1 }).raise_for_status.json
    best = data["results"]&.first
    fail LookupError, "location not found: #{name.inspect}" unless best

    label = [best["name"], best["country"]].compact.join(", ")
    Location.new(label, best["latitude"], best["longitude"]).to_h
  end

  class LookupError < StandardError; end
end
