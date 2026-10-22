# frozen_string_literal: true

require "httpx"
require_relative "base_tool"

# Approximate current location (city level) of this machine, from its public IP.
class MyLocation < BaseTool
  IP_GEOLOCATION_URL = "https://ipwho.is/" # https://ipwhois.io/documentation

  description "Returns the approximate current location (city level) of this machine (name, latitude, longitude). " \
              "Use it when the user asks about 'here' or 'my location' without naming a place."

  def initialize(timeout: 10.0)
    super()
    @http = HTTPX.with(timeout: { request_timeout: timeout })
  end

  def execute
    data = @http.get(IP_GEOLOCATION_URL).raise_for_status.json
    unless data["success"]
      fail LookupError, "could not locate IP address: #{data.fetch('message', 'unknown error')}"
    end

    label = data.values_at("city", "country").compact.reject(&:empty?).join(", ")
    { name: label, latitude: data["latitude"], longitude: data["longitude"] }
  end

  class LookupError < StandardError; end
end
