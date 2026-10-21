"""Applies db/schema.sql (idempotent). Run once after deploy:

    npx serverless invoke -f migrate
"""
import logging
import os

from src.lib.db import get_connection

log = logging.getLogger()
log.setLevel(logging.INFO)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "db", "schema.sql")


def _has_code(chunk: str) -> bool:
    return any(line.strip() and not line.strip().startswith("--") for line in chunk.splitlines())


def split_statements(sql: str) -> list[str]:
    """Split on ';' except inside $$-quoted bodies (plpgsql functions)."""
    statements, current, in_dollar = [], [], False
    for part in sql.split("$$"):
        if in_dollar:
            current.append("$$" + part + "$$")
        else:
            pieces = part.split(";")
            current.append(pieces[0])
            for piece in pieces[1:]:
                statements.append("".join(current))
                current = [piece]
        in_dollar = not in_dollar
    statements.append("".join(current))
    return [s.strip() for s in statements if _has_code(s)]


def handler(_event, _context):
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        sql = fh.read()
    conn = get_connection()
    # pg8000's native run() executes one statement at a time.
    statements = split_statements(sql)
    for stmt in statements:
        conn.run(stmt)
    log.info("Applied %d schema statements", len(statements))
    return {"applied": len(statements)}
