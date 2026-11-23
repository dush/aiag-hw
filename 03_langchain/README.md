# Exercise 03: company checker (LangGraph + MCP)

A **plan-and-execute** agent built with [LangGraph](https://docs.langchain.com/oss/python/langgraph/) that checks
Czech companies. All its tools come from **MCP servers**, loaded with LangChain's built-in
[`langchain.mcp.MCPAdapter`](https://docs.langchain.com/oss/python/langchain/mcp) (`langchain[mcp]`, built on
FastMCP 4; it replaces the older `langchain-mcp-adapters` package, see the
[migration guide](https://docs.langchain.com/oss/python/migrate/langchain-mcp-adapters)):

| MCP server | Tools the agent gets | Source |
|---|---|---|
| `ares` ([`ares-gov-cz-mcp`](https://github.com/iquest/ares-mcp), via `uvx`) | `search_entities`, `get_entity`, `get_register_record` | [ARES](https://ares.gov.cz) REST API, no key |
| `wikipedia` ([`wikipedia-mcp`](https://pypi.org/project/wikipedia-mcp/), via `uvx`) | `search_wikipedia`, `get_summary` | Czech Wikipedia |
| `tavily` ([`tavily-mcp`](https://github.com/tavily-ai/tavily-mcp), via `npx`) | `tavily-search` | Web search, needs `TAVILY_API_KEY`; skipped without it |

Example questions:

- `Prověř firmu s IČO 27082440. Kdo to je, odkdy existuje a je v pořádku?`
- `Kdo je ve statutárním orgánu společnosti Kofola ČeskoSlovensko a.s.?`
- `Jaký je základní kapitál ČEZ, a. s. a kde sídlí? Jsou o firmě nějaké aktuální zprávy?`

## How it works

```
START → planner → executor → replanner ─┬─→ executor   (steps left)
                                        └─→ END        (final answer)
```

- **planner**: the model writes a plan of 2–5 steps (structured output `Plan`).
- **executor**: a ReAct agent (`langchain.agents.create_agent`) with all the MCP tools does **one** step.
- **replanner**: the model decides to answer or lists the remaining steps (structured output `Act`). The
  answer itself is a separate plain-text call, so it keeps its formatting and is not limited by a tool-call
  argument.
- A step cap (`MAX_STEPS = 6`) and recursion limits make sure a weak model can't loop forever. When the cap is
  reached, the model writes the answer from the steps done so far.

Small free models handle one short step at a time better than one long tool loop over the whole question. The
plan and each step's result are visible with `-v`.

Design notes:

- **Small tool set.** `wikipedia-mcp` exposes about 20 tools (each also under a `wikipedia_` alias). Only the
  tools in `ALLOWED_TOOLS` ([company_checker/mcp_servers.py](company_checker/mcp_servers.py)) reach the model.
- **One adapter per server.** A server that fails to start (no network, no `npx`) is skipped and the agent goes on
  with the others. With one server per adapter the tool names keep their original form; a multi-server
  `MCPConfig` would prefix them as `{server}_{tool}`.
- **Sessions held for the whole run.** Tools from `MCPAdapter.list_tools()` open the client on every call.
  `open_tools()` keeps each adapter's context open around the agent run, so the calls reuse one session and one
  server process. The stdio processes are stopped when the run ends. Server stderr goes to `logs/<server>.log`.
- **Beta API.** `langchain.mcp` is in beta (`LangChainBetaWarning`, which `mcp_servers.py` silences on import).
- **ARES server.** The ARES tools come from [`ares-gov-cz-mcp`](https://github.com/iquest/ares-mcp) (PyPI,
  pinned to 0.1.0, started with `uvx`). It is built on the official
  [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/), covers the whole ARES API with 8 read-only tools and
  returns raw ARES JSON. Only 3 of the tools reach the model. A not-found IČO comes back as a tool error
  (`[not_found]`), which the model sees as the tool result.
- **Compact ARES output.** The raw commercial register record holds the full history (62 KB for Alza).
  [company_checker/ares_tools.py](company_checker/ares_tools.py) wraps the 3 tools on the client side: same
  names, descriptions and arguments, but the model gets only the current records (those without `datumVymazu`),
  about 1 KB for Alza. `get_register_record` accepts only the commercial register (`register="vr"`).
- **No personal data.** For natural persons (board members, shareholders) the wrapper keeps only the name and
  role. It drops birth dates and home addresses before anything reaches the model.
- **Risk signals.** `get_entity` gives `zanikla` (closed) and `v_insolvencnim_rejstriku`; `get_register_record`
  gives insolvency and bankruptcy records.
- **MCP client.** FastMCP's `StdioTransport` wrapped in `MCPAdapter`, which is what `langchain[mcp]` builds on.

## Setup

Prerequisites: [uv](https://docs.astral.sh/uv/), Node.js with `npx` (only for Tavily), and an OpenAI-compatible
LLM endpoint. By default the agent uses the LiteLLM proxy from the repository root (see the
[root README](../README.md)):

```sh
cd ..                          # repository root
docker compose up -d           # LiteLLM on http://127.0.0.1:4000
cd 03_langchain
uv sync
```

Settings are read from the environment or a `.env` file in this directory (git-ignored):

| Variable | Default | Meaning |
|---|---|---|
| `LITELLM_BASE_URL` | `http://127.0.0.1:4000/v1` | OpenAI-compatible base URL (LiteLLM, Ollama `http://127.0.0.1:11434/v1`, ...) |
| `LITELLM_API_KEY` | `dummy-key` | API key; LiteLLM without a master key accepts any value |
| `LLM_MODEL` | `openrouter-gemma` | Model name or LiteLLM alias. It must support tool calling. |
| `STRUCTURED_OUTPUT_METHOD` | `function_calling` | How the planner gets JSON: `function_calling`, `json_schema` or `json_mode` |
| `TAVILY_API_KEY` | (empty) | Turns on web search; free key at <https://app.tavily.com> |
| `WIKIPEDIA_MCP` | `1` | `0` turns the Wikipedia server off |
| `WIKIPEDIA_LANGUAGE` | `cs` | Wikipedia language |

## Run

```sh
uv run python main.py "Prověř firmu s IČO 27082440"
uv run python main.py -v "Kdo vlastní Kofola ČeskoSlovensko a.s.?"   # show the plan and the steps
uv run python main.py                                               # interactive
```

The ARES MCP server also runs on its own (stdio), for example for Claude Desktop or the MCP Inspector:

```sh
uvx ares-gov-cz-mcp==0.1.0
```

## Tests and evaluation

```sh
uv run pytest                              # ARES compaction and the graph offline; MCP startup needs uvx
uv run python -m scripts.smoke_mcp         # starts the MCP servers, lists tools, calls ARES live
uv run python -m scripts.smoke_structured  # which structured-output method works with your model
uv run python -m scripts.eval_agent        # live agent on tests/eval_questions.json
```

`eval_agent` checks that each answer contains the expected facts and that the agent called the expected tools.
The unit test fixtures are synthetic (made-up company and person).

## Files

| Path | Contents |
|---|---|
| `main.py` | CLI |
| `company_checker/ares_tools.py` | Wraps the ARES MCP tools: compact results without personal data |
| `company_checker/graph.py` | The LangGraph plan-and-execute graph |
| `company_checker/prompts.py` | Planner, executor and replanner prompts |
| `company_checker/mcp_servers.py` | MCP server configuration, tool allowlist, persistent sessions |
| `company_checker/llm.py` | `ChatOpenAI` for the configured endpoint |
| `company_checker/run.py` | `ask(question)`: opens the tools, runs the graph |
| `scripts/` | Smoke tests and the evaluation |
| `tests/` | Unit tests and evaluation questions |

## Limits

- Free OpenRouter models are rate limited (HTTP 429), and the free tier allows a limited number of requests per day.
  One question takes about 5–11 model calls.
- For very common names, ARES search returns many matches; asking with the IČO is more reliable.
- Live evaluation with `LLM_MODEL=openrouter-nemotron-3-ultra` (`nvidia/nemotron-3-ultra-550b-a55b:free`,
  `function_calling`): 4/4 passed with `ares-gov-cz-mcp` 0.1.0.
  `json_mode` fails with this model; the free Gemma was rate limited upstream at the time.
