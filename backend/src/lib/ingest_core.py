"""Streaming CSV -> PostgreSQL loader.

Design notes
------------
* The file is consumed as a stream (never fully loaded into memory), so a
  multi-hundred-MB CSV works inside a 512 MB Lambda.
* Valid rows are buffered in chunks and pushed with PostgreSQL COPY into a
  temp staging table. COPY is an order of magnitude faster than row-by-row
  INSERTs and needs one round-trip per chunk.
* A single set-based UPSERT moves staging -> users inside one transaction, so a
  batch is either fully visible to the matching workflow or not at all.
* Re-uploading the same user_id updates the row and re-tags it with the new
  batch_id (last write wins; duplicates inside one file: last row wins).
"""
import csv
import io
import logging
from dataclasses import dataclass, field
from typing import Iterable, TextIO

from pg8000.native import Connection

from src.lib import config
from src.lib.csv_validation import HeaderError, RowError, map_header, parse_row

log = logging.getLogger(__name__)

MAX_REJECT_SAMPLES = 20

_STAGE_DDL = """
CREATE TEMP TABLE users_stage (
    row_no            BIGINT,
    user_id           TEXT,
    name              TEXT,
    email             TEXT,
    monthly_income    NUMERIC(12,2),
    credit_score      SMALLINT,
    employment_status TEXT,
    age               SMALLINT
) ON COMMIT DROP
"""

_COPY_SQL = (
    "COPY users_stage (row_no, user_id, name, email, monthly_income, credit_score, employment_status, age) "
    "FROM STDIN WITH (FORMAT csv)"
)

_UPSERT_SQL = """
INSERT INTO users (user_id, name, email, monthly_income, credit_score, employment_status, age, batch_id)
SELECT DISTINCT ON (user_id)
       user_id, name, email, monthly_income, credit_score, employment_status, age, CAST(:batch_id AS uuid)
FROM users_stage
ORDER BY user_id, row_no DESC
ON CONFLICT (user_id) DO UPDATE SET
    name              = EXCLUDED.name,
    email             = EXCLUDED.email,
    monthly_income    = EXCLUDED.monthly_income,
    credit_score      = EXCLUDED.credit_score,
    employment_status = EXCLUDED.employment_status,
    age               = EXCLUDED.age,
    batch_id          = EXCLUDED.batch_id,
    updated_at        = now()
"""


@dataclass
class IngestResult:
    rows_total: int = 0
    rows_inserted: int = 0
    rows_rejected: int = 0
    rejected_sample: list[dict] = field(default_factory=list)

    def reject(self, line_no: int, reason: str) -> None:
        self.rows_rejected += 1
        if len(self.rejected_sample) < MAX_REJECT_SAMPLES:
            self.rejected_sample.append({"line": line_no, "reason": reason})


def _copy_chunk(conn: Connection, rows: list[tuple]) -> None:
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    buf.seek(0)
    conn.run(_COPY_SQL, stream=buf)


def ingest_csv(stream: TextIO, batch_id: str, conn: Connection,
               chunk_rows: int = config.INGEST_CHUNK_ROWS) -> IngestResult:
    """Validate and load a CSV text stream into `users`. Raises HeaderError for unusable files."""
    reader: Iterable[list[str]] = csv.reader(stream)
    try:
        header = next(reader)
    except StopIteration:
        raise HeaderError("CSV file is empty") from None
    mapping = map_header(header)

    result = IngestResult()
    chunk: list[tuple] = []

    conn.run("BEGIN")
    try:
        conn.run(_STAGE_DDL)
        # line 1 is the header, so data starts at line 2 (matches what a spreadsheet shows)
        for line_no, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue  # ignore blank lines
            result.rows_total += 1
            try:
                u = parse_row(row, mapping)
            except RowError as exc:
                result.reject(line_no, str(exc))
                continue
            chunk.append((line_no, u.user_id, u.name, u.email, u.monthly_income,
                          u.credit_score, u.employment_status, u.age))
            if len(chunk) >= chunk_rows:
                _copy_chunk(conn, chunk)
                chunk.clear()
        if chunk:
            _copy_chunk(conn, chunk)

        conn.run(_UPSERT_SQL, batch_id=batch_id)
        result.rows_inserted = conn.row_count
        conn.run("COMMIT")
    except Exception:
        conn.run("ROLLBACK")
        raise

    log.info("batch %s: total=%d inserted=%d rejected=%d", batch_id,
             result.rows_total, result.rows_inserted, result.rows_rejected)
    return result
