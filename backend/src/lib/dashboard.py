"""Read-only queries and routing for the operations dashboard (frontend/index.html).

`route()` is shared by the `dashboard` Lambda (API Gateway GET/POST /api/...)
and by local/dev_server.py, so both environments expose the same endpoints:

    GET  /api/overview                     DB connectivity, table counts, n8n status
    GET  /api/batches                      recent uploads with status history
    GET  /api/batches/{id}                 one batch + the n8n runs of Workflow B and C
    GET  /api/batches/{id}/matches         paged matches (?offset=&limit=&q=)
    GET  /api/users/{user_id}              a user, every match with its reason, emails sent
    GET  /api/products                     Workflow A output: loan_products + crawl_cache
    GET  /api/notifications                emails sent (?batch_id=)
    GET  /api/workflows                    the three workflows with their nodes in order
    GET  /api/executions                   recent runs (?workflow=A|B|C)
    GET  /api/executions/{id}              node-by-node summary of one run
    POST /api/workflows/A/run              start a crawl now (Workflow A's webhook)
"""
import datetime as dt
import time
import uuid

from pg8000.native import Connection

from src.lib import config, n8n_api, n8n_client
from src.lib.batches import get as get_batch
from src.lib.db import rows_as_dicts

TABLES = ["upload_batches", "users", "loan_products", "matches", "llm_decision_cache", "crawl_cache", "notifications"]


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise LookupError("not a valid UUID") from None


def _int(query: dict, key: str, default: int, cap: int) -> int:
    try:
        return max(0, min(cap, int(query.get(key, default))))
    except ValueError:
        return default


def _ts(value: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


# ---------------------------------------------------------------- database views
def overview(conn: Connection) -> dict:
    t0 = time.perf_counter()
    conn.run("SELECT 1")
    latency = round((time.perf_counter() - t0) * 1000, 1)
    info = rows_as_dicts(conn, "SELECT version() AS version, current_database() AS database, "
                               "(SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()) AS ssl")[0]
    counts = {t: conn.run(f"SELECT count(*) FROM {t}")[0][0] for t in TABLES}
    crawl = rows_as_dicts(conn, "SELECT count(*) FILTER (WHERE is_active) AS active, max(last_seen_at) AS last_crawl, "
                                "count(*) FILTER (WHERE criteria_source = 'llm') AS by_llm FROM loan_products")[0]
    n8n = {"ui_url": config.N8N_UI_URL, "api_url": n8n_api.base_url()}
    try:
        n8n["workflows"] = [{k: w[k] for k in ("id", "key", "name", "active")} for w in n8n_api.workflows()]
        n8n["running"] = [e for e in n8n_api.executions(limit=10) if e["status"] in ("running", "new", "waiting")]
        n8n["ok"] = True
    except n8n_api.N8nUnavailable as exc:
        n8n.update(ok=False, error=str(exc))
    return {
        "environment": {"mode": "aws" if config.DB_SECRET_ARN else "local", "db_host": config.DB_HOST,
                        "db_port": config.DB_PORT, "upload_bucket": config.UPLOAD_BUCKET or "local/.s3"},
        "db": {"ok": True, "latency_ms": latency, "database": info["database"], "ssl": bool(info["ssl"]),
               "version": info["version"].split(" on ")[0], "tables": counts},
        "products": crawl,
        "n8n": n8n,
    }


def list_batches(conn: Connection, query: dict) -> dict:
    rows = rows_as_dicts(conn, "SELECT batch_id::text, file_name, status, rows_total, rows_inserted, rows_rejected, "
                               "match_stats->>'final_matches' AS final_matches, match_stats->>'emails_sent' AS emails_sent, "
                               "status_history, created_at, updated_at FROM upload_batches "
                               "ORDER BY created_at DESC LIMIT :n", n=_int(query, "limit", 20, 100))
    return {"batches": rows}


def _runs_for_batch(batch: dict) -> dict:
    """Match Workflow B/C executions to a batch by time window (executions carry no batch id until fetched)."""
    times = {h["status"]: _ts(h["at"]) for h in batch.get("status_history") or []}
    now = dt.datetime.now(dt.timezone.utc)
    pad = dt.timedelta(seconds=10)
    windows = {
        "B": (times.get("ingested") or times.get("matching"), times.get("matched") or times.get("notify_failed") or now),
        "C": (times.get("matched"), times.get("notified") or times.get("notify_failed") or now),
    }
    if not times and batch["status"] in ("matched", "notified", "notify_failed"):
        # batches from before status_history existed: fall back to the batch's own lifetime
        windows = {key: (batch["created_at"], batch["updated_at"]) for key in ("B", "C")}
    runs = {}
    for key, (start, end) in windows.items():
        if not start:
            continue
        for ex in n8n_api.executions(n8n_api.WORKFLOW_IDS[key], limit=25):
            started = _ts(ex["startedAt"])
            if started and start - pad <= started <= end + pad:
                runs[key] = ex
                break
    return runs


def batch_detail(conn: Connection, batch_id: str) -> dict:
    batch = get_batch(conn, _uuid(batch_id))
    if not batch:
        raise LookupError("batch not found")
    try:
        batch["runs"] = _runs_for_batch(batch)
    except n8n_api.N8nUnavailable as exc:
        batch["runs"], batch["runs_error"] = {}, str(exc)
    return batch


def batch_matches(conn: Connection, batch_id: str, query: dict) -> dict:
    params = {"b": _uuid(batch_id), "q": f"%{query.get('q', '').strip()}%",
              "lim": _int(query, "limit", 50, 200), "off": _int(query, "offset", 0, 10_000_000)}
    where = ("m.batch_id = CAST(:b AS uuid) AND (u.name ILIKE :q OR u.email ILIKE :q OR u.user_id ILIKE :q "
             "OR p.lender ILIKE :q OR m.decision_source ILIKE :q)")
    total = conn.run(f"SELECT count(*) FROM matches m JOIN users u USING (user_id) JOIN loan_products p USING (product_id) "
                     f"WHERE {where}", b=params["b"], q=params["q"])[0][0]
    rows = rows_as_dicts(conn, f"""
        SELECT m.user_id, u.name, u.email, u.monthly_income, u.credit_score, u.employment_status, u.age,
               m.product_id, p.lender, p.product_name, m.score, m.decision_source, m.reason, m.notified_at
        FROM matches m JOIN users u USING (user_id) JOIN loan_products p USING (product_id)
        WHERE {where} ORDER BY m.score DESC, m.user_id LIMIT :lim OFFSET :off""", **params)
    by_source = dict(conn.run("SELECT decision_source, count(*) FROM matches WHERE batch_id = CAST(:b AS uuid) "
                              "GROUP BY 1", b=params["b"]))
    return {"total": total, "offset": params["off"], "limit": params["lim"], "by_source": by_source, "rows": rows}


def user_detail(conn: Connection, user_id: str) -> dict:
    users = rows_as_dicts(conn, "SELECT user_id, name, email, monthly_income, credit_score, employment_status, age, "
                                "batch_id::text, created_at, updated_at FROM users WHERE user_id = :u", u=user_id)
    if not users:
        raise LookupError("user not found")
    matches = rows_as_dicts(conn, """
        SELECT m.product_id, p.lender, p.product_name, p.interest_rate_min, p.interest_rate_max,
               p.min_monthly_income, p.min_credit_score, p.min_age, p.max_age, p.criteria_source, p.source_url,
               m.score, m.decision_source, m.reason, m.notified_at, m.created_at
        FROM matches m JOIN loan_products p USING (product_id) WHERE m.user_id = :u ORDER BY m.score DESC""", u=user_id)
    emails = rows_as_dicts(conn, "SELECT sent_to, product_ids, ses_message_id, batch_id::text, sent_at "
                                 "FROM notifications WHERE user_id = :u ORDER BY sent_at DESC", u=user_id)
    return {"user": users[0], "matches": matches, "notifications": emails}


def products(conn: Connection) -> dict:
    rows = rows_as_dicts(conn, """
        SELECT product_id, lender, product_name, interest_rate_min, interest_rate_max, min_monthly_income,
               min_credit_score, max_credit_score, min_age, max_age, employment_types, max_loan_amount,
               other_criteria, criteria_source, source_url, is_active, last_seen_at, updated_at,
               (SELECT count(*) FROM matches m WHERE m.product_id = p.product_id) AS matches
        FROM loan_products p ORDER BY product_id""")
    cache = rows_as_dicts(conn, "SELECT url, left(content_hash, 12) AS content_hash, fetched_at, extracted "
                                "FROM crawl_cache ORDER BY url")
    return {"products": rows, "crawl_cache": cache}


def notifications(conn: Connection, query: dict) -> dict:
    sql = ("SELECT n.notification_id, n.user_id, u.name, u.email AS original_email, n.sent_to, n.product_ids, "
           "n.ses_message_id, n.batch_id::text, n.sent_at FROM notifications n LEFT JOIN users u USING (user_id)")
    if query.get("batch_id"):
        rows = rows_as_dicts(conn, sql + " WHERE n.batch_id = CAST(:b AS uuid) ORDER BY n.sent_at DESC",
                             b=_uuid(query["batch_id"]))
    else:
        rows = rows_as_dicts(conn, sql + " ORDER BY n.sent_at DESC LIMIT 100")
    return {"notifications": rows}


# ---------------------------------------------------------------- router
def route(conn_factory, method: str, path: str, query: dict) -> tuple[int, dict]:
    """Dispatch /api/... ; returns (HTTP status, JSON body)."""
    parts = [p for p in path.split("?")[0].strip("/").split("/") if p][1:]   # drop leading "api"
    try:
        if method == "POST" and parts == ["workflows", "A", "run"]:
            status = n8n_api.trigger_webhook("crawl-products", {"source": "dashboard"}, n8n_client._token())
            return (202, {"started": True}) if status < 300 else (502, {"error": f"n8n answered HTTP {status}"})
        if method != "GET":
            return 405, {"error": "method not allowed"}
        if parts == ["workflows"]:
            return 200, {"workflows": n8n_api.workflows(), "ui_url": config.N8N_UI_URL}
        if parts == ["executions"]:
            wf = n8n_api.WORKFLOW_IDS.get(query.get("workflow", ""))
            return 200, {"executions": n8n_api.executions(wf, _int(query, "limit", 10, 50))}
        if len(parts) == 2 and parts[0] == "executions":
            return 200, n8n_api.execution_summary(parts[1])

        conn = conn_factory()
        if parts == ["overview"]:
            return 200, overview(conn)
        if parts == ["batches"]:
            return 200, list_batches(conn, query)
        if len(parts) == 2 and parts[0] == "batches":
            return 200, batch_detail(conn, parts[1])
        if len(parts) == 3 and parts[0] == "batches" and parts[2] == "matches":
            return 200, batch_matches(conn, parts[1], query)
        if len(parts) == 2 and parts[0] == "users":
            return 200, user_detail(conn, parts[1])
        if parts == ["products"]:
            return 200, products(conn)
        if parts == ["notifications"]:
            return 200, notifications(conn, query)
        return 404, {"error": "unknown endpoint"}
    except LookupError as exc:
        return 404, {"error": str(exc)}
    except n8n_api.N8nUnavailable as exc:
        return 503, {"error": str(exc)}
