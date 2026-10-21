"""SQS consumer: S3 ObjectCreated event -> parse CSV -> users table -> trigger n8n.

Why S3 -> SQS -> Lambda instead of S3 -> Lambda directly?
  * SQS buffers bursts and gives automatic retries with a dead-letter queue,
    so a transient RDS hiccup never silently drops an upload.
  * ReportBatchItemFailures means only the failed message is retried.
"""
import codecs
import json
import logging
import re
import urllib.parse

import boto3

from src.lib import batches, n8n_client
from src.lib.csv_validation import HeaderError
from src.lib.db import get_connection
from src.lib.ingest_core import ingest_csv

log = logging.getLogger()
log.setLevel(logging.INFO)

_s3 = boto3.client("s3")
_BATCH_FROM_KEY = re.compile(r"uploads/([0-9a-fA-F-]{36})\.csv$")


def process_upload(stream_bytes, batch_id: str, s3_key: str) -> dict:
    """Shared by the Lambda and the local dev server. `stream_bytes` is any binary file-like."""
    conn = get_connection()
    batches.ensure_exists(conn, batch_id, s3_key)

    current = batches.get(conn, batch_id)
    if current and current["status"] in batches.TERMINAL_INGEST_STATES:
        log.info("Batch %s already %s; skipping duplicate delivery", batch_id, current["status"])
        return {"batch_id": batch_id, "skipped": True}

    batches.set_status(conn, batch_id, "ingesting")
    text = codecs.getreader("utf-8-sig")(stream_bytes, errors="replace")  # strips Excel BOM
    try:
        result = ingest_csv(text, batch_id, conn)
    except HeaderError as exc:
        # Unusable file: record it and stop. Not retryable, so don't raise.
        batches.set_status(conn, batch_id, "failed", error=str(exc))
        return {"batch_id": batch_id, "error": str(exc)}

    batches.set_status(
        conn, batch_id, "ingested",
        rows_total=result.rows_total, rows_inserted=result.rows_inserted,
        rows_rejected=result.rows_rejected, rejected_sample=result.rejected_sample,
    )

    if result.rows_inserted:
        try:
            n8n_client.trigger_matching(batch_id, result.rows_inserted)
        except Exception as exc:  # data is safely stored; surface the handoff failure in the UI
            log.exception("Could not trigger n8n for batch %s", batch_id)
            batches.set_status(conn, batch_id, "notify_webhook_failed", error=f"n8n webhook: {exc}")
    return {"batch_id": batch_id, **result.__dict__}


def _s3_records(sqs_record: dict):
    body = json.loads(sqs_record["body"])
    if body.get("Event") == "s3:TestEvent":  # sent once when the notification is configured
        return []
    return body.get("Records", [])


def handler(event, _context):
    failures = []
    for record in event.get("Records", []):
        try:
            for s3_rec in _s3_records(record):
                bucket = s3_rec["s3"]["bucket"]["name"]
                key = urllib.parse.unquote_plus(s3_rec["s3"]["object"]["key"])
                match = _BATCH_FROM_KEY.search(key)
                if not match:
                    log.warning("Ignoring object with unexpected key %s", key)
                    continue
                obj = _s3.get_object(Bucket=bucket, Key=key)
                outcome = process_upload(obj["Body"], match.group(1).lower(), key)
                log.info("Processed %s: %s", key, json.dumps(outcome, default=str))
        except Exception:
            log.exception("Failed to process SQS message %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
