#!/bin/sh
# Entry point of the n8n-import container (docker-compose.yml; it calls this only when RUIAN_IMPORT=true).
# Imports the Postgres credentials and the workflow into a fresh n8n, runs the workflow once, then exits.
# The exit code is non-zero when the workflow fails.
set -eu

WORKFLOW="${RUIAN_WORKFLOW:-/n8n-import/ruian-import.workflow.json}"
WORKFLOW_ID="${RUIAN_WORKFLOW_ID:-ruianImportN8n}"
WORK_DIR=/import/ruian
CREDS=/tmp/ruian-credentials.json

# The workflow builds SQL text from these values, so only allow plain names and numbers.
RUIAN_DB="${RUIAN_DB:-ruian}"
RUIAN_MAX_FILES="${RUIAN_MAX_FILES:-0}"
if ! printf '%s' "$RUIAN_DB" | grep -Eqx '[a-z_][a-z0-9_]{0,62}'; then
    echo "Invalid RUIAN_DB '$RUIAN_DB': use lowercase letters, digits and _ (max 63 characters)." >&2
    exit 2
fi
if ! printf '%s' "$RUIAN_MAX_FILES" | grep -Eqx '[0-9]+'; then
    echo "Invalid RUIAN_MAX_FILES '$RUIAN_MAX_FILES': use a whole number (0 = all files)." >&2
    exit 2
fi
export RUIAN_DB RUIAN_MAX_FILES

cleanup() {
    rm -rf "$WORK_DIR" "$CREDS"
}
trap cleanup EXIT
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

# Credentials from the environment. n8n encrypts plaintext credential data on import.
node -e '
const base = { host: "postgres", port: 5432, user: "langflow", password: process.env.POSTGRES_PASSWORD,
               ssl: "disable", allowUnauthorizedCerts: false, maxConnections: 5 };
const creds = [
  { id: "ruianPgAdmin", name: "RUIAN Postgres (admin)", type: "postgres", data: { ...base, database: "langflow" } },
  { id: "ruianPgTarget", name: "RUIAN Postgres (target)", type: "postgres", data: { ...base, database: process.env.RUIAN_DB } },
];
require("fs").writeFileSync(process.argv[1], JSON.stringify(creds), { mode: 0o600 });
' "$CREDS"

n8n import:credentials --input="$CREDS"
rm -f "$CREDS"
n8n import:workflow --input="$WORKFLOW"

unset POSTGRES_PASSWORD
echo "Running workflow $WORKFLOW_ID (database ${RUIAN_DB}, max files ${RUIAN_MAX_FILES:-0}) ..."
start=$(date +%s)
n8n execute --id="$WORKFLOW_ID" --rawOutput > /tmp/ruian-output.json || {
    status=$?
    echo "Workflow failed (exit $status):" >&2
    tail -c 4000 /tmp/ruian-output.json >&2 || true
    exit "$status"
}
echo "Workflow finished in $(( $(date +%s) - start )) s."
tail -c 2000 /tmp/ruian-output.json
echo
