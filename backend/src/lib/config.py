"""Runtime configuration, read from environment variables.

Every Lambda and the local dev server share this module, so the same code runs
on AWS (values injected by serverless.yml) and on a laptop (values from .env).
"""
import os


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# --- Database -----------------------------------------------------------------
DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = _int("DB_PORT", 5432)
DB_NAME = os.environ.get("DB_NAME", "loanengine")
DB_USER = os.environ.get("DB_USER", "loanadmin")
# Locally the password comes straight from env. On AWS, RDS manages the master
# password in Secrets Manager and we only receive the secret ARN.
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_SECRET_ARN = os.environ.get("DB_SECRET_ARN")
# "require" on AWS (verified against the bundled RDS CA), "disable" locally.
DB_SSL = os.environ.get("DB_SSL", "disable")

# --- Upload / ingestion -------------------------------------------------------
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "")
UPLOAD_PREFIX = "uploads/"
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 100)
PRESIGN_TTL_SECONDS = _int("PRESIGN_TTL_SECONDS", 900)
INGEST_CHUNK_ROWS = _int("INGEST_CHUNK_ROWS", 5000)

# --- n8n handoff --------------------------------------------------------------
N8N_MATCH_WEBHOOK_URL = os.environ.get("N8N_MATCH_WEBHOOK_URL", "")
# Either the token itself (local) or the SSM SecureString holding it (AWS).
N8N_WEBHOOK_TOKEN = os.environ.get("N8N_WEBHOOK_TOKEN")
N8N_WEBHOOK_TOKEN_PARAM = os.environ.get("N8N_WEBHOOK_TOKEN_PARAM")

# --- Dashboard: n8n public API (read workflows + executions) -------------------
# Base URL of n8n; derived from N8N_MATCH_WEBHOOK_URL when unset.
N8N_API_URL = os.environ.get("N8N_API_URL", "")
# Either the key itself (local) or the SSM SecureString holding it (AWS).
N8N_API_KEY = os.environ.get("N8N_API_KEY")
N8N_API_KEY_PARAM = os.environ.get("N8N_API_KEY_PARAM")
# Where the browser can open the n8n editor ("Open in n8n" links).
N8N_UI_URL = os.environ.get("N8N_UI_URL", "http://localhost:5678")
