"""Data access for the upload_batches table (the pipeline's status record)."""
import json

from pg8000.native import Connection

from src.lib.db import rows_as_dicts

# Statuses after which re-processing the same S3 object must be a no-op
# (SQS is at-least-once, so the ingest Lambda can see a message twice).
TERMINAL_INGEST_STATES = {"ingested", "matching", "matched", "notified", "notify_webhook_failed", "notify_failed"}


def create(conn: Connection, batch_id: str, file_name: str, s3_key: str) -> None:
    conn.run(
        "INSERT INTO upload_batches (batch_id, file_name, s3_key, status) "
        "VALUES (CAST(:id AS uuid), :file_name, :s3_key, 'awaiting_upload')",
        id=batch_id, file_name=file_name, s3_key=s3_key,
    )


def get(conn: Connection, batch_id: str) -> dict | None:
    rows = rows_as_dicts(
        conn,
        "SELECT batch_id::text, file_name, status, rows_total, rows_inserted, rows_rejected, "
        "rejected_sample, match_stats, error, status_history, created_at, updated_at "
        "FROM upload_batches WHERE batch_id = CAST(:id AS uuid)",
        id=batch_id,
    )
    return rows[0] if rows else None


def ensure_exists(conn: Connection, batch_id: str, s3_key: str) -> None:
    """Objects dropped straight into the bucket (e.g. via the AWS CLI) still get a batch row."""
    conn.run(
        "INSERT INTO upload_batches (batch_id, file_name, s3_key, status) "
        "VALUES (CAST(:id AS uuid), :name, :key, 'awaiting_upload') ON CONFLICT (batch_id) DO NOTHING",
        id=batch_id, name=s3_key.rsplit("/", 1)[-1], key=s3_key,
    )


def set_status(conn: Connection, batch_id: str, status: str, **fields) -> None:
    """Update status plus any of rows_total/rows_inserted/rows_rejected/rejected_sample/error."""
    allowed = {"rows_total", "rows_inserted", "rows_rejected", "rejected_sample", "error"}
    sets = ["status = :status", "updated_at = now()"]
    params = {"id": batch_id, "status": status}
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"unknown batch field {key}")
        if key == "rejected_sample":
            sets.append("rejected_sample = CAST(:rejected_sample AS jsonb)")
            value = json.dumps(value)
        else:
            sets.append(f"{key} = :{key}")
        params[key] = value
    conn.run(f"UPDATE upload_batches SET {', '.join(sets)} WHERE batch_id = CAST(:id AS uuid)", **params)
