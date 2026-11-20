"""Integration tests for Langflow flows.

Run against a local instance (started with ``lfx serve``):

    pytest tests/ --langflow-url http://localhost:8000

Run against a named environment (staging, production, etc.):

    pytest tests/ --langflow-env staging -m integration

The flow_runner fixture auto-skips when no connection is configured,
so these tests are safe to include in any CI pipeline.
"""

import pytest


@pytest.mark.integration
def test_flow_responds(flow_runner):
    """Smoke test: every flow should return a non-empty response."""
    # TODO: replace "my-flow-endpoint" with your flow's endpoint name or UUID
    result = flow_runner("my-flow-endpoint", "Hello!")
    assert result.first_text_output() is not None, "Flow returned no output"


@pytest.mark.integration
def test_flow_output_quality(flow_runner):
    """Example: assert on the content of the response."""
    result = flow_runner("my-flow-endpoint", "What is Langflow?")
    text = result.first_text_output()
    assert text is not None
    assert len(text) > 20, f"Response seems too short: {text!r}"
