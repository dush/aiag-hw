# 02_langflow

Local development environment for [Langflow](https://github.com/langflow-ai/langflow), run with Docker Compose. The setup is based on the upstream [`docker_example`](https://github.com/langflow-ai/langflow/tree/main/docker_example).

## Services

| Service    | Image                         | Purpose                         | Port |
|------------|-------------------------------|---------------------------------|------|
| `langflow` | `langflowai/langflow:latest`  | Langflow UI and API             | 7860 |
| `postgres` | `postgres:16-trixie`          | Langflow database               | none (internal only) |
| `litellm`  | `ghcr.io/berriai/litellm:main-stable` | LLM proxy for the agent flows (models in `litellm_config.yaml`) | 127.0.0.1:4001 |
| `n8n-import` (+ `n8n-import-init`) | `n8nio/n8n:2.45.4` | One-shot RUIAN import, only with `RUIAN_IMPORT=true` | none |

Data is kept in named volumes: `langflow-data` (logs, files, secret keys), `langflow-postgres` (databases `langflow` and `ruian`) and `ruian-import` (temporary CSV files of the n8n import, empty after a run).

## Project layout

| Path | Contents |
|------|----------|
| `flows/` | Flow JSON files: `hello-world`, `simple-agent`, [`ruian-agent`](#ruian-search-component-and-agent-flow). Push them with `lfx push --dir flows/ --env local`. |
| `components/` | Custom component source: `ruian_search_component.py` (RUIAN Search tools). The `ruian-agent` flow embeds this code; `scripts/build_ruian_agent_flow.py` regenerates it. |
| `scripts/` | Flow and workflow generators (`build_simple_agent_flow.py` and `build_ruian_agent_flow.py` share `agent_llm.py`), `ruian_schema.py` (schema of the `ruian` database), `smoke_ruian_search.py` (checks the RUIAN Search tools). |
| `litellm_config.yaml` | Models of the `litellm` service. |
| `n8n/` | The n8n import workflow and its container entry point (`run.sh`). |
| `tests/` | Unit tests for RUIAN Search (`test_ruian_search_component.py`) and integration-test templates (`test_flows.py`). |
| `ci/`, `.github/workflows/` | Validate (`lfx validate`), test and push flows in CI. |
| `data/` | The RUIAN zip (`*_OB_ADR_csv.zip`), mounted read-only into `n8n-import`. |

## Prerequisites

- Docker with the Compose plugin (`docker compose version`)
- An [OpenRouter](https://openrouter.ai/keys) API key for the `litellm` service (free models need no credit)

## Setup

Create a `.env` file next to `docker-compose.yml` (it is git-ignored) from the template, then fill in the values:

```sh
cp .env.example .env
```

At minimum it needs:

```
POSTGRES_PASSWORD=change-me
OPENROUTER_API_KEY=sk-or-...
```

`POSTGRES_PASSWORD` is required. If it is missing, `docker compose` stops with an error that names it. Without `OPENROUTER_API_KEY` the stack starts, but the agent flows fail with `AuthenticationError ... No cookie auth credentials found` (401 from OpenRouter). After adding it, run `docker compose up -d litellm`; `restart` keeps the old environment.

`LANGFLOW_SUPERUSER_PASSWORD` is not needed: `docker-compose.yml` sets `LANGFLOW_AUTO_LOGIN=true`, so the UI opens without a login.

### LLM

The Agent of the agent flows (`simple-agent`, `ruian-agent`) uses Langflow's built-in **OpenAI Compatible** model provider. By default it talks to the `litellm` service of this compose file and uses its `openrouter/free` model ([OpenRouter's Free Models Router](https://openrouter.ai/openrouter/free), which picks a free model that supports the request, e.g. tool calling), so no OpenAI key is needed. Free models are rate limited (429) and the router's choice changes between runs; for repeatable runs use `openrouter-gemma`, or add a model to `litellm_config.yaml` and run `docker compose restart litellm`.

| Variable          | Default | Read by | Meaning |
|-------------------|---------|---------|---------|
| `LLM_API_URL`     | `http://litellm:4000/v1` | `docker compose` (at start) | Base URL of the OpenAI-compatible API, as seen from the `langflow` container. The provider adds `/v1` if it is missing. |
| `LLM_API_KEY`     | placeholder | `docker compose` (at start) | API key. LiteLLM without a master key accepts any value. |
| `LLM_MODEL`       | `openrouter/free` | flow build scripts (shell environment; `.env` is not read) | Model name stored in the agent flows. For LiteLLM, a `model_name` from `litellm_config.yaml` (`openrouter/free`, `openrouter-gemma`). |
| `OPENROUTER_API_KEY` | none | `docker compose` (at start) | OpenRouter key, read by the `litellm` service. |

How it works:

- `docker-compose.yml` passes `LLM_API_URL` and `LLM_API_KEY` to Langflow as `OPENAI_COMPATIBLE_BASE_URL` and `OPENAI_COMPATIBLE_API_KEY` and imports them as global variables on every start (`LANGFLOW_VARIABLES_TO_GET_FROM_ENVIRONMENT`). Settings → Model Providers then shows OpenAI Compatible as configured, with the models that LiteLLM serves. After changing them in `.env`, run `docker compose up -d --no-deps langflow`. If you edit the variables in the UI, Langflow stops updating them from the environment.
- The model name is part of the flow, so `LLM_MODEL` applies when the flows are generated. Change it with:
  ```sh
  export LLM_MODEL=openrouter-gemma
  uv run python scripts/build_simple_agent_flow.py && uv run python scripts/build_ruian_agent_flow.py
  lfx push --dir flows/ --env local
  ```
  To try another model without regenerating, pick it in the Agent's Language Model field in the UI (the list comes from LiteLLM).
- `LANGFLOW_SSRF_ALLOWED_HOSTS=litellm` lets Langflow call the `litellm` service; its SSRF protection blocks private addresses otherwise. `LANGFLOW_PROVIDER_CREDENTIAL_ALLOWED_HOSTS=http://litellm` lets it send the API key from the environment to that host over plain HTTP; without it the Agent fails with "Refusing to send a server-provisioned API credential to the non-default endpoint". If `LLM_API_URL` points to another host, add it to both settings in `docker-compose.yml` (for HTTPS hosts, without `http://`).
- Run the agent flows in the Langflow server (UI, API, `lfx push`). `lfx run` does not load the OpenAI Compatible provider bundle and sends the request to api.openai.com instead.

Optional variables for working with the API and MCP server. `docker-compose.yml` does not read them, so export them in your shell (or keep them in `.env` and load it yourself) rather than relying on Compose:

| Variable                    | Used by                                        |
|-----------------------------|------------------------------------------------|
| `LANGFLOW_API_KEY`          | `.mcp.json` (langflow MCP server), API calls   |
| `LANGFLOW_LOCAL_API_KEY`    | `lfx` commands via `.lfx/environments.yaml`    |
| `OPENAI_API_KEY`            | Only flows that use OpenAI directly (not the agent flows, see [LLM](#llm)) |

## Run

```sh
docker compose up -d        # start in the background
docker compose logs -f langflow
```

Open http://localhost:7860. No login is needed because `LANGFLOW_AUTO_LOGIN=true` is set.

Langflow waits for the Postgres and LiteLLM healthchecks to pass and for `n8n-import` to finish before it starts, so the first start can take a minute. With `RUIAN_IMPORT=true`, the [n8n import](#ruian-import) adds about another minute.

## Auto-login and API keys

With `LANGFLOW_AUTO_LOGIN=true` the UI needs no credentials, but since Langflow 1.5 the API still does. Calls such as `POST /api/v1/run/<flow_id>` fail with "LANGFLOW_AUTO_LOGIN requires a valid API key" unless you send a key.

1. In the UI, open Settings → API Keys and create a key. Or use the CLI in the container (works because auto-login is on). It must get the same `LANGFLOW_DATABASE_URL` as the server, which compose sets only in the start command; without it the key goes to an internal SQLite file and the server answers "Invalid API key":
   ```sh
   docker compose exec langflow sh -c 'export LANGFLOW_DATABASE_URL="postgresql://langflow:$(python -c "import os,urllib.parse as u; print(u.quote(os.environ[\"POSTGRES_PASSWORD\"], safe=\"\"))")@postgres:5432/langflow"; langflow api-key'
   ```
2. Export it:
   ```sh
   export LANGFLOW_API_KEY=<key>          # .mcp.json and direct API calls
   export LANGFLOW_LOCAL_API_KEY=<key>    # lfx, see .lfx/environments.yaml
   ```
3. Send it in the `x-api-key` header:
   ```sh
   curl -X POST http://localhost:7860/api/v1/run/<flow_id> \
     -H "x-api-key: $LANGFLOW_API_KEY" -H "Content-Type: application/json" \
     -d '{"input_value": "What is Langflow?", "input_type": "chat", "output_type": "chat"}'
   ```

The agent flows use the [OpenAI Compatible provider](#llm) and need no OpenAI key. A flow that picks an OpenAI model in a component itself needs `OPENAI_API_KEY` in Settings → Global Variables (the name must match exactly). Never commit keys.

For local development only, setting `LANGFLOW_SKIP_AUTH_AUTO_LOGIN=true` on the `langflow` service turns the API-key requirement off. Do not use it on a shared or exposed instance.

## Common tasks

```sh
docker compose ps                 # service status
docker compose pull && docker compose up -d   # update the image
docker compose restart litellm    # after editing litellm_config.yaml
docker compose logs -f litellm    # watch LLM requests
docker compose down               # stop, keep data
docker compose down -v            # stop and DELETE all data (flows, users, database)
```

## RUIAN import

The RUIAN address places (`data/20261031_OB_ADR_csv.zip`: one CSV per municipality, cp1250, `;`-separated) are loaded into the database `ruian` on the compose Postgres by an n8n workflow that uses only built-in nodes. The one-shot service `n8n-import` in `docker-compose.yml` runs it and exits when it is done. It uses the `postgres` service; it does not start a second Postgres.

Tables (all keys are the RUIAN codes). Foreign keys point from child to parent:

| Table           | Rows      | Parent keys                         |
|-----------------|-----------|-------------------------------------|
| `obec`          | 6,256     | none                                |
| `obvod_prahy`   | 10        | `obec_kod`                          |
| `momc`          | 142       | `obec_kod`, `obvod_prahy_kod`       |
| `cast_obce`     | 15,091    | `obec_kod`                          |
| `ulice`         | 83,493    | `obec_kod`                          |
| `adresni_misto` | 3,023,585 | all of the above (`momc`, `obvod_prahy`, `ulice` optional) |

Only entities present in this CSV are modelled. The other entities in the RUIAN hierarchy (okres, kraj, ORP, POU, ZSJ, katastrální území) come from a separate `strukt_ADR` download that is not in this zip.

With `RUIAN_IMPORT=true` (in `.env` or the shell), `docker compose up` runs the import before `langflow` starts. Without it, `n8n-import` exits at once and the stack starts as usual. You can also run the import on demand:

```sh
RUIAN_IMPORT=true docker compose up -d                              # import, then start Langflow
docker compose run --rm -e RUIAN_IMPORT=true n8n-import           # import only, into `ruian`
docker compose run --rm -e RUIAN_IMPORT=true \
  -e RUIAN_DB=ruian_test -e RUIAN_MAX_FILES=50 n8n-import           # quick test into another database
```

Notes on `RUIAN_IMPORT=true`:

- Every `docker compose up` runs the full import again (all tables are reloaded, about 1 minute). Remove the variable after the first load.
- If `langflow` is already running, `docker compose up -d` runs the import while Langflow stays up. RUIAN Search queries can time out during the reload.
- If the import fails (for example, no zip in `data/`), `langflow` does not start. See `docker compose logs n8n-import`.
- `langflow` (the RUIAN Search component) reads `RUIAN_DATABASE_URL` (default database `ruian`), not `RUIAN_DB`. Change `RUIAN_DB` only for test runs.

| Variable          | Default | Meaning |
|-------------------|---------|---------|
| `RUIAN_IMPORT`    | empty   | `true` runs the import on `docker compose up`. Other values skip it. Set it with `-e` for `docker compose run` (see above). |
| `RUIAN_DB`        | `ruian` | Target database (lowercase letters, digits and `_`). It is created if missing. |
| `RUIAN_MAX_FILES` | `0`     | Load only the first N CSV files (0 = all). |

A full import of `data/20261031_OB_ADR_csv.zip` took about 1 minute (59 s workflow, 68 s with container start). The result was identical to the former Langflow import component: checksums of all six tables matched.

How it works (`n8n/ruian-import.workflow.json`):

1. **Postgres** (admin credential, database `langflow`): check whether the target database exists. **If** it does not, create it.
2. **Postgres** (target): run `DDL` and `SEARCH_DDL` from `scripts/ruian_schema.py` and create the unlogged staging table `ruian_stage`.
3. **Read/Write Files from Disk** reads the zip from `/data`. **Compression** unzips it. **Split Out** (`$binary`) makes one item per CSV. **Limit** applies `RUIAN_MAX_FILES`.
4. **Read/Write Files from Disk** writes each CSV to the `ruian-import` volume (`/import/ruian`).
5. **Postgres** runs `COPY ruian_stage FROM '/import/ruian/<file>' WITH (FORMAT csv, DELIMITER ';', HEADER true, ENCODING 'WIN1250')` per file. Postgres reads the files itself and converts cp1250 to UTF-8, so no row passes through n8n.
6. **Postgres** loads the tables in one transaction: `TRUNCATE`, then `INSERT ... SELECT DISTINCT ON` for each table, then drops the staging table and runs `ANALYZE`. If a step fails, the old data stays.

What the setup does:

- `docker-compose.yml` mounts the `ruian-import` volume read-only at `/import` in the `postgres` service, so `COPY` can read the files. Adding this mount recreates the `postgres` container once (a short restart of Langflow's database).
- The `n8n-import` entry point exits 0 unless `RUIAN_IMPORT=true`. `langflow` waits for it with `service_completed_successfully`.
- `n8n/run.sh` (called by the entry point) builds the two Postgres credentials from `POSTGRES_PASSWORD`, imports them and the workflow into a fresh n8n (SQLite in the container, deleted with it), runs the workflow with `n8n execute`, and deletes the CSV files. A failed workflow gives a non-zero exit code.
- The image is pinned to `n8nio/n8n:2.45.4`. These n8n settings are required:
  - `N8N_COMPRESSION_NODE_MAX_ZIP_ENTRIES` must be raised from 5000, because the zip has 6,258 files.
  - `N8N_RESTRICT_FILE_ACCESS_TO=/data;/import` lets the file nodes read the zip and write the CSV files.
  - `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` lets the workflow read `RUIAN_DB` and `RUIAN_MAX_FILES`.
- The service `n8n-import-init` makes the volume writable for n8n (uid 1000).

The workflow is generated by `scripts/build_ruian_n8n_workflow.py`; the schema SQL is in `scripts/ruian_schema.py`. After you change `DDL` or `SEARCH_DDL`, regenerate it with `uv run python scripts/build_ruian_n8n_workflow.py`. You can also import the workflow into an interactive n8n to look at it, but its paths (`/data`, `/import`) and credentials are set up for this container.

## RUIAN Search component and agent flow

`RUIAN Search` is a custom Langflow component (`components/ruian_search_component.py`). Its **Toolset** output gives an Agent six predefined, read-only search tools over the `ruian` database. The `ruian-agent` flow (Chat Input → Agent with the RUIAN Search tools → Chat Output) uses it to answer questions about Czech addresses. The component runs inside the `langflow` container; there is no separate server.

### Setup

1. Load the data with `RUIAN_IMPORT=true docker compose up -d` (see [RUIAN import](#ruian-import)). The tools need the `ruian` database; until it exists, they return a database error to the agent.
2. Check the tools against the database (runs inside the `langflow` container):
   ```sh
   uv run python scripts/smoke_ruian_search.py     # lists the tools and runs sample searches
   ```
3. Push the agent flow (`lfx push --dir flows/ --env local`) or import `flows/ruian-agent.json` in the UI. The Agent is the same as in `simple-agent` and uses the [OpenAI Compatible model provider](#llm) (LiteLLM, `openrouter/free` by default). The flow embeds the component code, so no extra setup is needed. To use the tools in another flow, add the `RUIAN Search` node (copy it from `ruian-agent`) and connect its Toolset output to the Agent's Tools input.

### Usage

**Chat in the UI.** Open `ruian-agent` and use the Playground. The agent answers in the language of the question. Example questions:

- `Kolik ulic začínajících na „Nádraž“ je v Brně a jak se jmenují?`
- `What is the postal code of Vodičkova 36 in Prague?`
- `Which city districts does Brno have?`
- `Jaké části obce má Turnov?`
- `Které ulice v Praze začínají na „Vodič“?`

The agent resolves names to ids step by step. For example, it calls `search_municipality` ("Brno"), then `search_street` (`municipality_id_in`), then `search_address_place` (`street_id_in`).

**Call the flow through the API.** Calls need an API key; see [Auto-login and API keys](#auto-login-and-api-keys).

```sh
curl -X POST http://localhost:7860/api/v1/run/ruian-agent \
  -H "x-api-key: $LANGFLOW_API_KEY" -H "Content-Type: application/json" \
  -d '{"input_value": "What is the postal code of Vodičkova 36 in Prague?", "input_type": "chat", "output_type": "chat"}'
```

**What the tools return.** Each tool returns JSON text. Example: find a street in Brno, then an address in Prague by street id and orientation number.

```jsonc
// search_street {"name_match": "nadraz", "municipality_id_in": [582786]}
{
  "items": [
    {"id": 33081, "name": "Nádražní", "municipality_id": 582786, "municipality_name": "Brno"},
    {"id": 36382, "name": "Nad nádražím", "municipality_id": 582786, "municipality_name": "Brno"}
  ],
  "has_more": false, "limit": 20, "offset": 0
}

// search_address_place {"street_id_in": [478156], "orientation_number": 36}
{
  "items": [
    {
      "id": 21704970,
      "building_number_type": "descriptive", "building_number": 704,
      "orientation_number": 36, "orientation_number_letter": null,
      "postal_code": "11000",
      "street_id": 478156, "street_name": "Vodičkova",
      "municipality_part_id": 490148, "municipality_part_name": "Nové Město",
      "city_district_id": 500054, "city_district_name": "Praha 1",
      "prague_district_id": 19, "prague_district_name": "Praha 1",
      "municipality_id": 554782, "municipality_name": "Praha",
      "sjtsk_x": 1043719.26, "sjtsk_y": 742614.93,
      "valid_from": "2017-07-04",
      "formatted": "Vodičkova 704/36, Nové Město, 110 00 Praha 1"
    }
  ],
  "has_more": false, "limit": 20, "offset": 0
}
```

More examples of arguments:

| Goal | Tool and arguments |
|------|--------------------|
| Municipalities with a word starting with "lhota" (e.g. "Suchá Lhota"), 50 per page, page 2 | `search_municipality {"name_match": "lhota", "limit": 50, "offset": 50}` |
| Two municipalities by id, sorted by name descending | `search_municipality {"id_in": [582786, 554782], "sort": ["-name"]}` |
| Streets of Prague ordered by name | `search_street {"municipality_id_in": [554782], "sort": ["name"]}` |
| City districts of Brno | `search_city_district {"municipality_id_in": [582786]}` |
| Addresses with postal code 110 00 | `search_address_place {"postal_code_in": ["11000"]}` |
| Building number 25 in a municipality part | `search_address_place {"municipality_part_id_in": [490148], "building_number": 25}` |

Errors, for example `sort: ["relevance"]` without `name_match` or `search_address_place` without a filter, come back to the agent as the tool result, with a message that names the problem (for example `Invalid arguments: limit: Input should be less than or equal to 200`). The flow run does not fail.

### Tools

| Tool | Filters | Sort keys |
|------|---------|-----------|
| `search_municipality` | `name_match`, `id_in` | `relevance`, `name`, `id` |
| `search_prague_district` | `name_match`, `id_in`, `municipality_id_in` | `relevance`, `name`, `id`, `municipality_id` |
| `search_city_district` | `name_match`, `id_in`, `municipality_id_in`, `prague_district_id_in` | `relevance`, `name`, `id`, `municipality_id`, `prague_district_id` |
| `search_municipality_part` | `name_match`, `id_in`, `municipality_id_in` | `relevance`, `name`, `id`, `municipality_id` |
| `search_street` | `name_match`, `id_in`, `municipality_id_in` | `relevance`, `name`, `id`, `municipality_id` |
| `search_address_place` | `id_in`, `municipality_id_in`, `municipality_part_id_in`, `street_id_in`, `city_district_id_in`, `prague_district_id_in`, `postal_code_in`, `building_number`, `orientation_number` (at least one is required) | `street`, `building_number`, `orientation_number`, `postal_code`, `id` |

Entities: municipality = `obec`, Prague district = `obvod Prahy` (Praha 1–10), city district = `MOMC` (městský obvod / městská část), municipality part = `část obce`, street = `ulice`, address place = `adresní místo`.

All tools also take `sort` (a list of keys; prefix `-` for descending), `limit` (default 20, max 200) and `offset`. They return `{"items": [...], "has_more": bool, "limit": n, "offset": n}`. `has_more: true` means that more items match than were returned. Parameters and returned keys are English, and every primary key is `id` (the RUIAN `kod`). The database keeps its Czech column names.

- `name_match` is fulltext on the name. Case and diacritics are ignored, every word is a prefix and all words must match: `nadraz br` finds `Nádražní`. There is no typo tolerance.
- Filters combine with AND. Filters that end in `_in` take a list and match any value in it.
- Default order: `relevance` with `name_match` (exact name, then names that start with the query, then `ts_rank`); the order of `id_in` when only `id_in` is given; otherwise `name` in Czech collation. Address places default to street, orientation number, building number. `id` is always the last tie-breaker, so paging with `offset` is stable.
- In `704/36`, 704 is the `building_number` (číslo popisné, or číslo evidenční when `building_number_type` is `registration`) and 36 the `orientation_number` (číslo orientační).
- `postal_code_in` takes 5-digit codes; spaces are removed (`"110 00"` = `"11000"`).
- Address places also return `formatted` and the raw S-JTSK coordinates `sjtsk_x` / `sjtsk_y` in metres (not GPS).

### Configuration and security

- Connections are read-only (`default_transaction_read_only`) with a 5 s statement timeout, and all queries are fixed and parameterized. A new connection is opened per tool call.
- Database: the component's "Database URL" input (advanced), else `RUIAN_DATABASE_URL`, else the compose Postgres (`langflow` user, `POSTGRES_PASSWORD`, database `ruian`).

### Development

- Component code: `components/ruian_search_component.py` (SQL builder and tools in one file, because Langflow runs the code embedded in the flow, not an import). The agent's system prompt is in `scripts/build_ruian_agent_flow.py`. After changing either, regenerate the flow with `uv run python scripts/build_ruian_agent_flow.py`, then push it again.
- Agent model: `scripts/agent_llm.py` sets it in both agent flows. Regenerate them in this order, because `ruian-agent` copies its Agent from `simple-agent`: `uv run python scripts/build_simple_agent_flow.py && uv run python scripts/build_ruian_agent_flow.py`.
- Unit tests (SQL builder, tool schemas, error handling): `uv run --with pytest python -m pytest tests/test_ruian_search_component.py`. Add `--with 'psycopg[binary]'` to also run the test that loads the component from its code string.
- The search schema (`SEARCH_DDL`) is in `scripts/ruian_schema.py`. After editing it, regenerate the n8n workflow (`uv run python scripts/build_ruian_n8n_workflow.py`) and run the import again; it applies the schema on every run.

## Notes

- `latest` is pulled on every start (`pull_policy: always`). To pin a version, change the tag in `docker-compose.yml`; tags are on [Docker Hub](https://hub.docker.com/r/langflowai/langflow).
- The database URL is built at container start from `POSTGRES_PASSWORD`, with the password URL-encoded, so special characters are safe.
- Postgres is not published to the host. To reach it, add a `ports` entry to the `postgres` service or use `docker compose exec postgres psql -U langflow`.
- Postgres reads `POSTGRES_PASSWORD` only when it first initializes the empty `langflow-postgres` volume. Changing it in `.env` later breaks Langflow's database login unless you run `docker compose down -v`, which deletes all data.
- The Postgres image is pinned to Debian trixie to avoid glibc collation warnings on existing volumes ([issue 9608](https://github.com/langflow-ai/langflow/issues/9608)).
