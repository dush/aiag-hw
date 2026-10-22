# frozen_string_literal: true

require "httpx"
require_relative "base_tool"

# Daily ECMWF forecast from Open-Meteo.
class WeatherForecast < BaseTool
  ECMWF_URL = "https://api.open-meteo.com/v1/ecmwf"
  DAILY_VARIABLES = %w[temperature_2m_max temperature_2m_min precipitation_sum].freeze

  description "Returns a daily ECMWF forecast (date, temp_max, temp_min, precipitation) from Open-Meteo. " \
              "Use the geocoder tool to get coordinates from a place name."
  parameter :latitude, type: :number, description: "Latitude in degrees"
  parameter :longitude, type: :number, description: "Longitude in degrees"

  def initialize(days: 7, timeout: 10.0)
    super()
    fail ArgumentError, "days must be between 1 and 15" unless (1..15).cover?(days)

    @days = days
    @http = HTTPX.with(timeout: { request_timeout: timeout })
  end

  def execute(latitude:, longitude:)
    data = @http.get(
      ECMWF_URL,
      params: {
        latitude: latitude,
        longitude: longitude,
        daily: DAILY_VARIABLES.join(","),
        timezone: "auto",
        forecast_days: @days
      }
    ).raise_for_status.json
    daily = data.fetch("daily")
    daily["time"].zip(*daily.values_at(*DAILY_VARIABLES)).map do |date, temp_max, temp_min, precipitation|
      { date: date, temp_max: temp_max, temp_min: temp_min, precipitation: precipitation }
    end
  end
end
