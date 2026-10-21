# frozen_string_literal: true

require "json"
require "net/http"
require "uri"

module HttpUtils
  def self.get_json(url, params, timeout)
    uri = URI(url)
    uri.query = URI.encode_www_form(params) unless params.empty?
    response = Net::HTTP.start(
      uri.host, uri.port,
      use_ssl: uri.scheme == "https", open_timeout: timeout, read_timeout: timeout
    ) { |http| http.get(uri.request_uri) }
    fail ConnectionError, "request to #{url} failed: HTTP #{response.code}" unless response.is_a?(Net::HTTPSuccess)

    JSON.parse(response.body)
  rescue SystemCallError, SocketError, Timeout::Error, OpenSSL::SSL::SSLError => e
    fail ConnectionError, "request to #{url} failed: #{e.message}"
  end

  class ConnectionError < StandardError; end
end
