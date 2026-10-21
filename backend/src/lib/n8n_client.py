"""Hands a freshly ingested batch over to n8n Workflow B (matching).

The webhook is protected with a shared-secret header (n8n "Header Auth"
credential). On AWS the secret lives in SSM Parameter Store as a SecureString
and is fetched once per container.
"""
import json
import logging
import time
import urllib.error
import urllib.request
from functools import lru_cache

from src.lib import config

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-Webhook-Token"


@lru_cache(maxsize=1)
def _token() -> str:
    if config.N8N_WEBHOOK_TOKEN:
        return config.N8N_WEBHOOK_TOKEN
    if config.N8N_WEBHOOK_TOKEN_PARAM:
        import boto3

        resp = boto3.client("ssm").get_parameter(Name=config.N8N_WEBHOOK_TOKEN_PARAM, WithDecryption=True)
        return resp["Parameter"]["Value"]
    raise RuntimeError("Set N8N_WEBHOOK_TOKEN or N8N_WEBHOOK_TOKEN_PARAM")


def trigger_matching(batch_id: str, rows_inserted: int, attempts: int = 3) -> None:
    """POST {batch_id} to the n8n webhook, retrying transient failures with backoff."""
    if not config.N8N_MATCH_WEBHOOK_URL:
        raise RuntimeError("N8N_MATCH_WEBHOOK_URL is not configured")

    body = json.dumps({"batch_id": batch_id, "rows_inserted": rows_inserted}).encode()
    req = urllib.request.Request(
        config.N8N_MATCH_WEBHOOK_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", TOKEN_HEADER: _token()},
    )
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                log.info("n8n accepted batch %s (HTTP %s)", batch_id, resp.status)
                return
        except urllib.error.HTTPError as exc:
            # 4xx = misconfiguration (bad token / inactive workflow): retrying won't help
            if exc.code < 500 or attempt == attempts:
                raise
            log.warning("n8n returned %s (attempt %d/%d)", exc.code, attempt, attempts)
        except urllib.error.URLError as exc:
            if attempt == attempts:
                raise
            log.warning("n8n unreachable: %s (attempt %d/%d)", exc.reason, attempt, attempts)
        time.sleep(2 ** attempt)
