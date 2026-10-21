"""POST /uploads  ->  presigned S3 POST for a direct browser-to-S3 upload.

The file never passes through API Gateway or Lambda (which cap payloads at
10 MB / 6 MB and 29 s); this function only hands out a short-lived, size-capped
upload form and records the batch. The S3 ObjectCreated event takes it from
there (S3 -> SQS -> ingest Lambda).
"""
import logging
import os
import re
import uuid

import boto3
from botocore.config import Config

from src.lib import batches, config
from src.lib.db import get_connection
from src.lib.http import parse_json_body, respond

log = logging.getLogger()
log.setLevel(logging.INFO)

# Sign against the regional virtual-hosted endpoint: the global one answers a
# brand-new bucket with a 307 redirect, which browsers won't follow for a POST.
_region = os.environ.get("AWS_REGION", "ap-south-1")
_s3 = boto3.client("s3", region_name=_region, endpoint_url=f"https://s3.{_region}.amazonaws.com",
                   config=Config(s3={"addressing_style": "virtual"}))
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def build_upload_key(batch_id: str) -> str:
    # Batch id is encoded in the key so the ingest Lambda can recover it from the S3 event.
    return f"{config.UPLOAD_PREFIX}{batch_id}.csv"


def handler(event, _context):
    try:
        body = parse_json_body(event)
    except ValueError as exc:
        return respond(400, {"error": str(exc)})

    file_name = _SAFE_NAME.sub("_", str(body.get("file_name", "users.csv")))[:200]
    if not file_name.lower().endswith(".csv"):
        return respond(400, {"error": "Only .csv files are accepted"})

    size = body.get("size")
    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    if isinstance(size, (int, float)) and size > max_bytes:
        return respond(413, {"error": f"File exceeds {config.MAX_UPLOAD_MB} MB limit"})

    batch_id = str(uuid.uuid4())
    key = build_upload_key(batch_id)

    # S3 enforces these conditions server-side, so a client can't swap in a bigger file.
    presigned = _s3.generate_presigned_post(
        Bucket=config.UPLOAD_BUCKET,
        Key=key,
        Fields={"Content-Type": "text/csv"},
        Conditions=[{"Content-Type": "text/csv"}, ["content-length-range", 1, max_bytes]],
        ExpiresIn=config.PRESIGN_TTL_SECONDS,
    )

    batches.create(get_connection(), batch_id, file_name, key)
    log.info("Issued upload URL for batch %s (%s)", batch_id, file_name)
    return respond(201, {"batch_id": batch_id, "upload": presigned})
