#!/bin/sh
# Runs once before n8n starts (docker compose service `n8n-init`):
#   1. builds the credential definitions from environment variables
#   2. imports them with fixed ids the workflow JSON already references
#   3. imports + activates workflows A, B and C (activate.js)
# Idempotent: re-running overwrites credentials/workflows with the same ids.
set -eu

CREDS=/tmp/loan-engine-credentials.json
node /init/make-credentials.js > "$CREDS"
n8n import:credentials --input="$CREDS"
rm -f "$CREDS"

n8n import:workflow --separate --input=/workflows
# Activate (see activate.js for why the stock CLI command isn't used)
SQLITE3_MODULE=$(find /usr/local/lib/node_modules/n8n/node_modules/.pnpm -maxdepth 3 -type d -path "*sqlite3@*/node_modules/sqlite3" | head -1)
export SQLITE3_MODULE
node /init/activate.js
echo "n8n-init: credentials and workflows imported"
