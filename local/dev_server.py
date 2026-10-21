"""Local stand-in for API Gateway + S3 + SQS + Lambda.

Runs the *same* handler code as AWS so the whole pipeline can be demoed on a
laptop with only Docker (Postgres + n8n) and Python:

    POST /uploads            -> same contract as the uploadUrl Lambda, but the
                                "presigned POST" points at /local-s3 below
    POST /local-s3           -> emulates S3: stores the file, then fires the
                                ingest code in a background thread (= S3 event
                                -> SQS -> ingestCsv Lambda)
    GET  /uploads/<batchId>  -> same as the uploadStatus Lambda
    GET|POST /api/...        -> same as the dashboard Lambda (src/lib/dashboard.py)
    GET  /                   -> the upload UI (frontend/index.html)

Usage (from repo root):  .venv/bin/python local/dev_server.py
"""
import email.parser
import email.policy
import json
import logging
import os
import pathlib
import sys
import threading
import urllib.parse
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
BUCKET_DIR = ROOT / "local" / ".s3"


def _load_env(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"'))


_load_env(ROOT / ".env")
# The dev server runs on the host, so it reaches Postgres/n8n via published ports.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", os.environ.get("APP_DB_PORT", "5433"))
os.environ.setdefault("DB_PASSWORD", os.environ.get("APP_DB_PASSWORD", ""))
os.environ.setdefault("N8N_MATCH_WEBHOOK_URL", "http://localhost:5678/webhook/loan-matching")
os.environ.setdefault("N8N_API_URL", "http://localhost:5678")
sys.path.insert(0, str(ROOT / "backend"))

from src.lib import batches, config, dashboard  # noqa: E402
from src.lib.db import connect  # noqa: E402
from src.lib.http import _default  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dev_server")
PORT = int(os.environ.get("DEV_SERVER_PORT", "8000"))


def _run_ingest(path: pathlib.Path, batch_id: str, key: str) -> None:
    # Imported lazily: ingest.py creates a boto3 S3 client at import time.
    from src.handlers import ingest
    from src.lib import db

    db._conn = None  # each background job gets its own connection
    with path.open("rb") as fh:
        outcome = ingest.process_upload(fh, batch_id, key)
    log.info("ingest finished: %s", json.dumps(outcome, default=str))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FRONTEND), **kwargs)

    # ------------------------------------------------------------- helpers
    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, default=_default).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def _dashboard(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        conns = []

        def factory():
            conns.append(connect())
            return conns[-1]
        try:
            status, body = dashboard.route(factory, method, parsed.path, query)
        except Exception as exc:  # surface DB/n8n errors to the UI instead of dropping the socket
            log.exception("dashboard request failed")
            status, body = 500, {"error": str(exc)}
        finally:
            for conn in conns:
                conn.close()
        self._json(status, body)

    # ------------------------------------------------------------- routes
    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._dashboard("GET")
        if self.path == "/config.js":
            data = b"window.API_BASE = '';\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path.startswith("/uploads/"):
            batch_id = self.path.rsplit("/", 1)[-1]
            try:
                uuid.UUID(batch_id)
            except ValueError:
                return self._json(400, {"error": "batchId must be a UUID"})
            conn = connect()
            try:
                batch = batches.get(conn, batch_id)
            finally:
                conn.close()
            return self._json(200, batch) if batch else self._json(404, {"error": "batch not found"})
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._dashboard("POST")
        if self.path == "/uploads":
            req = json.loads(self._body() or b"{}")
            file_name = str(req.get("file_name", "users.csv"))
            if not file_name.lower().endswith(".csv"):
                return self._json(400, {"error": "Only .csv files are accepted"})
            batch_id = str(uuid.uuid4())
            key = f"{config.UPLOAD_PREFIX}{batch_id}.csv"
            conn = connect()
            try:
                batches.create(conn, batch_id, file_name, key)
            finally:
                conn.close()
            upload = {"url": f"http://localhost:{PORT}/local-s3",
                      "fields": {"key": key, "Content-Type": "text/csv"}}
            return self._json(201, {"batch_id": batch_id, "upload": upload})

        if self.path == "/local-s3":
            raw = self._body()
            # Parse multipart/form-data with the stdlib email parser (cgi was removed in 3.13).
            msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                f"Content-Type: {self.headers['Content-Type']}\r\n\r\n".encode() + raw)
            parts = {p.get_param("name", header="content-disposition"): p for p in msg.iter_parts()}
            key = parts["key"].get_content().strip()
            file_bytes = parts["file"].get_payload(decode=True)
            dest = BUCKET_DIR / key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(file_bytes)
            batch_id = pathlib.Path(key).stem
            # Fire-and-forget, exactly like S3 -> SQS -> Lambda would.
            threading.Thread(target=_run_ingest, args=(dest, batch_id, key), daemon=True).start()
            self.send_response(204)
            self.end_headers()
            return

        self._json(404, {"error": "not found"})


if __name__ == "__main__":
    log.info("Upload UI on http://localhost:%d  (DB %s:%s, n8n webhook %s)", PORT,
             config.DB_HOST, config.DB_PORT, config.N8N_MATCH_WEBHOOK_URL)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
