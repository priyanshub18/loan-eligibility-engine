"""GET/POST /api/{proxy+} -> read-only operations dashboard API (see src/lib/dashboard.py)."""
from src.lib import dashboard
from src.lib.db import get_connection
from src.lib.http import respond


def handler(event, _context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    status, body = dashboard.route(get_connection, method, event.get("rawPath", ""),
                                   event.get("queryStringParameters") or {})
    return respond(status, body)
