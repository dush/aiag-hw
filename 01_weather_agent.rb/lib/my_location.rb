# frozen_string_literal: true

require_relative "geocoder"
require_relative "http_utils"
require_relative "weather_tool"

# Approximate current location (city level) of this machine, from its public IP.
class MyLocation < WeatherTool
  IP_GEOLOCATION_URL = "https://ipwho.is/" # https://ipwhois.io/documentation

  description "Returns the approximate current location (city level) of this machine as a Location. " \
              "Use it when the user asks about 'here' or 'my location' without naming a place."

  def initialize(timeout: 10.0)
    super()
    @timeout = timeout
  end

  def execute
    data = HttpUtils.get_json(IP_GEOLOCATION_URL, {}, @timeout)
    unless data["success"]
      fail Geocoder::LookupError, "could not locate IP address: #{data.fetch('message', 'unknown error')}"
    end

    label = data.values_at("city", "country").compact.reject(&:empty?).join(", ")
    Location.new(label, data["latitude"], data["longitude"]).to_h
  end
end
