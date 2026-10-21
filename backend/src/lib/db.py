"""PostgreSQL connection management.

pg8000 is a pure-Python driver: no native wheels to compile for Lambda's
Amazon Linux runtime, which keeps packaging trivial.

The connection is cached at module level so warm Lambda invocations reuse it
instead of paying the TCP + TLS + auth handshake on every request.
"""
import json
import logging
import os
import ssl
from functools import lru_cache

from pg8000.native import Connection, InterfaceError

from src.lib import config

log = logging.getLogger(__name__)

_RDS_CA_BUNDLE = os.path.join(os.path.dirname(__file__), "..", "..", "certs", "rds-global-bundle.pem")
_conn = None


@lru_cache(maxsize=1)
def _password() -> str:
    """Resolve the DB password once per container (env locally, Secrets Manager on AWS)."""
    if config.DB_PASSWORD:
        return config.DB_PASSWORD
    if not config.DB_SECRET_ARN:
        raise RuntimeError("Set DB_PASSWORD or DB_SECRET_ARN")
    import boto3  # only needed on AWS; keeps local runs free of boto3 setup

    secret = boto3.client("secretsmanager").get_secret_value(SecretId=config.DB_SECRET_ARN)
    return json.loads(secret["SecretString"])["password"]


def _ssl_context():
    if config.DB_SSL == "disable":
        return None
    ctx = ssl.create_default_context(cafile=os.path.abspath(_RDS_CA_BUNDLE))
    return ctx


def connect() -> Connection:
    return Connection(
        user=config.DB_USER,
        password=_password(),
        host=config.DB_HOST,
        port=config.DB_PORT,
        database=config.DB_NAME,
        ssl_context=_ssl_context(),
        timeout=30,
        application_name="loan-engine-lambda",
    )


def get_connection() -> Connection:
    """Return a live cached connection, reconnecting if the old one went stale."""
    global _conn
    if _conn is not None:
        try:
            _conn.run("SELECT 1")
            return _conn
        except (InterfaceError, OSError):
            log.info("Cached DB connection is stale; reconnecting")
            _conn = None
    _conn = connect()
    return _conn


def rows_as_dicts(conn: Connection, sql: str, **params) -> list[dict]:
    rows = conn.run(sql, **params)
    cols = [c["name"] for c in conn.columns or []]
    return [dict(zip(cols, r)) for r in rows]
