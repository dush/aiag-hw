# frozen_string_literal: true

require "ruby_llm"

# Base class for the tools. RubyLLM lets exceptions from #execute escape the chat
# loop; report them to the model instead so it can recover (as the Python agent does).
class BaseTool < RubyLLM::Tool
  def call(**)
    super
  rescue StandardError => e
    RubyLLM.logger.warn("tool #{name} failed: #{e.class}: #{e.message}")
    { error: "#{e.class}: #{e.message}" }
  end
end
