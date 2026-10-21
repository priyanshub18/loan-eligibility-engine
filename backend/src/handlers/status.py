"""GET /uploads/{batchId} -> batch status + matching funnel stats (polled by the UI)."""
import uuid

from src.lib import batches
from src.lib.db import get_connection
from src.lib.http import respond


def handler(event, _context):
    batch_id = (event.get("pathParameters") or {}).get("batchId", "")
    try:
        uuid.UUID(batch_id)
    except ValueError:
        return respond(400, {"error": "batchId must be a UUID"})

    batch = batches.get(get_connection(), batch_id)
    if not batch:
        return respond(404, {"error": "batch not found"})
    return respond(200, batch)
