"""Read-only client for the n8n public REST API (workflows and executions).

Used by the dashboard API to show which workflow is running and how far each
run got, node by node. Authenticates with an n8n API key: N8N_API_KEY locally,
or the SSM SecureString named by N8N_API_KEY_PARAM on AWS.
"""
import json
import time
import urllib.error
import urllib.request
from functools import lru_cache

from src.lib import config

WORKFLOW_IDS = {"A": "LoanEngineWfA0001", "B": "LoanEngineWfB0001", "C": "LoanEngineWfC0001"}
_FINISHED = {"success", "error", "crashed", "canceled"}
_summary_cache: dict[str, dict] = {}     # finished executions never change
_workflow_cache: dict = {"at": 0.0, "data": None}


class N8nUnavailable(RuntimeError):
    pass


def base_url() -> str:
    """n8n's base URL: explicit N8N_API_URL, else derived from the matching webhook URL."""
    if config.N8N_API_URL:
        return config.N8N_API_URL.rstrip("/")
    return config.N8N_MATCH_WEBHOOK_URL.split("/webhook/")[0].rstrip("/")


@lru_cache(maxsize=1)
def _api_key() -> str:
    if config.N8N_API_KEY:
        return config.N8N_API_KEY
    if not config.N8N_API_KEY_PARAM:
        raise N8nUnavailable("N8N_API_KEY is not configured")
    import boto3

    resp = boto3.client("ssm").get_parameter(Name=config.N8N_API_KEY_PARAM, WithDecryption=True)
    return resp["Parameter"]["Value"]


def _get(path: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(f"{base_url()}/api/v1{path}",
                                 headers={"X-N8N-API-KEY": _api_key(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise N8nUnavailable(f"n8n API unreachable at {base_url()}: {exc}") from exc


def _node_order(wf: dict) -> list[dict]:
    """Nodes in execution order: breadth-first from the trigger nodes along connections."""
    nodes = {n["name"]: n for n in wf.get("nodes", []) if n["type"] != "n8n-nodes-base.stickyNote"}
    targets = {c["node"] for outs in wf.get("connections", {}).values() for branch in outs.get("main", []) for c in branch or []}
    queue = [name for name in nodes if name not in targets]
    seen: list[str] = []
    while queue:
        name = queue.pop(0)
        if name in seen or name not in nodes:
            continue
        seen.append(name)
        for branch in wf.get("connections", {}).get(name, {}).get("main", []):
            queue.extend(c["node"] for c in branch or [])
    return [{"name": n, "type": nodes[n]["type"].split(".")[-1]} for n in seen]


def workflows() -> list[dict]:
    if _workflow_cache["data"] is None or time.time() - _workflow_cache["at"] > 60:
        data = _get("/workflows?limit=50")["data"]
        _workflow_cache["data"] = [{
            "id": wf["id"], "name": wf["name"], "active": wf["active"], "updatedAt": wf.get("updatedAt"),
            "key": next((k for k, v in WORKFLOW_IDS.items() if v == wf["id"]), None),
            "nodes": _node_order(wf),
        } for wf in data]
        _workflow_cache["at"] = time.time()
    return _workflow_cache["data"]


def executions(workflow_id: str | None = None, limit: int = 10) -> list[dict]:
    query = f"?limit={limit}" + (f"&workflowId={workflow_id}" if workflow_id else "")
    return [{k: e.get(k) for k in ("id", "workflowId", "status", "mode", "startedAt", "stoppedAt", "finished")}
            for e in _get(f"/executions{query}")["data"]]


def _compact(value, depth: int = 0):
    """Shrink a node's output item for display: long lists and strings are truncated."""
    if isinstance(value, dict):
        if depth > 2:
            return "{…}"
        return {k: _compact(v, depth + 1) for k, v in list(value.items())[:14]}
    if isinstance(value, list):
        head = [_compact(v, depth + 1) for v in value[:3]]
        return head + [f"… {len(value) - 3} more"] if len(value) > 3 else head
    if isinstance(value, str) and len(value) > 160:
        return value[:160] + f"… ({len(value)} chars)"
    return value


def execution_summary(execution_id: str) -> dict:
    """Per-node status, timing, item counts and a compact output preview for one run."""
    if execution_id in _summary_cache:
        return _summary_cache[execution_id]
    ex = _get(f"/executions/{execution_id}?includeData=true", timeout=28)
    result = (ex.get("data") or {}).get("resultData") or {}
    run_data = result.get("runData") or {}
    nodes = []
    batch_id = None
    for name, runs in run_data.items():
        run = runs[-1]
        outputs = (run.get("data") or {}).get("main") or []
        first = next((branch[0]["json"] for branch in outputs if branch), None)
        if first and isinstance(first.get("body"), dict):
            batch_id = batch_id or first["body"].get("batch_id")
            first = {"body": first["body"]}  # webhook items also carry request headers
        nodes.append({
            "name": name,
            "status": run.get("executionStatus") or ("error" if run.get("error") else "success"),
            "startTime": run.get("startTime"),
            "ms": run.get("executionTime"),
            "items": [len(branch or []) for branch in outputs],
            "error": (run.get("error") or {}).get("message"),
            "preview": _compact(first) if first is not None else None,
        })
    nodes.sort(key=lambda n: n["startTime"] or 0)
    summary = {
        "id": ex["id"], "workflowId": ex["workflowId"], "status": ex["status"], "mode": ex.get("mode"),
        "startedAt": ex.get("startedAt"), "stoppedAt": ex.get("stoppedAt"),
        "lastNode": result.get("lastNodeExecuted"), "error": (result.get("error") or {}).get("message"),
        "batch_id": batch_id, "nodes": nodes,
    }
    if ex["status"] in _FINISHED:
        _summary_cache[execution_id] = summary
    return summary


def trigger_webhook(path: str, body: dict, token: str) -> int:
    req = urllib.request.Request(f"{base_url()}/webhook/{path}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Webhook-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, TimeoutError) as exc:
        raise N8nUnavailable(f"n8n webhook unreachable: {exc}") from exc
