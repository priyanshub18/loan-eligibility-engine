#!/usr/bin/env python3
"""Generates the n8n workflow JSON files in n8n/workflows/ from n8n/src/.

Code-node JavaScript and SQL live as real files under n8n/src/ so they can be
read, reviewed and diffed like normal code; this script stitches them into
importable n8n workflows. Re-run after editing anything in src/:

    python3 n8n/build_workflows.py
"""
import json
import pathlib
import uuid

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "src"
OUT = HERE / "workflows"

# Fixed credential ids so imported workflows are wired up without manual clicks
# (n8n/init/make-credentials.js creates credentials with these exact ids).
CRED_PG = {"postgres": {"id": "LoanEnginePgDb01", "name": "Loan Engine Postgres"}}
CRED_AWS = {"aws": {"id": "LoanEngineAwsSes1", "name": "Loan Engine AWS (SES)"}}
CRED_SMTP = {"smtp": {"id": "LoanEngineGmailSmtp", "name": "Loan Engine Gmail SMTP"}}
CRED_HOOK = {"httpHeaderAuth": {"id": "LoanEngineHookAuth", "name": "Loan Engine Webhook Token"}}


class Workflow:
    def __init__(self, wf_id: str, name: str, folder: str):
        self.wf_id, self.name, self.folder = wf_id, name, folder
        self.nodes: list[dict] = []
        self.connections: dict = {}

    # ------------------------------------------------------------ primitives
    def node(self, name, type_, version, pos, params, **extra):
        node = {
            "parameters": params,
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.wf_id}/{name}")),
            "name": name,
            "type": type_,
            "typeVersion": version,
            "position": list(pos),
            **extra,
        }
        self.nodes.append(node)
        return name

    def connect(self, src, dst, out_index=0):
        outs = self.connections.setdefault(src, {"main": []})["main"]
        while len(outs) <= out_index:
            outs.append([])
        outs[out_index].append({"node": dst, "type": "main", "index": 0})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.connect(a, b)

    def read(self, file):
        return (SRC / self.folder / file).read_text()

    # ------------------------------------------------------------ node helpers
    def code(self, name, file, pos, **extra):
        return self.node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": self.read(file)}, **extra)

    def postgres(self, name, file, params, pos, **extra):
        """`params`: one `{{ }}` expression per $n. JSON-string values are passed intact."""
        p = {"operation": "executeQuery", "query": self.read(file), "options": {}}
        if params:
            p["options"]["queryReplacement"] = "=" + ",".join(f"{{{{ {e} }}}}" for e in params)
        return self.node(name, "n8n-nodes-base.postgres", 2.6, pos, p, credentials=CRED_PG, **extra)

    def webhook(self, name, path, pos):
        return self.node(name, "n8n-nodes-base.webhook", 2, pos,
                         {"httpMethod": "POST", "path": path, "authentication": "headerAuth",
                          "responseMode": "onReceived", "options": {}},
                         credentials=CRED_HOOK, webhookId=str(uuid.uuid5(uuid.NAMESPACE_URL, path)))

    def http_llm(self, name, pos):
        return self.node(name, "n8n-nodes-base.httpRequest", 4.2, pos, {
            "method": "POST",
            "url": "={{ $env.LLM_BASE_URL }}/chat/completions",
            "sendHeaders": True,
            "headerParameters": {"parameters": [
                {"name": "Authorization", "value": "=Bearer {{ $env.LLM_API_KEY }}"}]},
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify($json.llm_request) }}",
            # one request every 4 s keeps us inside free-tier rate limits
            "options": {"timeout": 60000, "batching": {"batch": {"batchSize": 1, "batchInterval": 4000}}},
        }, onError="continueRegularOutput", retryOnFail=True, maxTries=3, waitBetweenTries=5000)

    def if_llm_needed(self, name, pos):
        return self.node(name, "n8n-nodes-base.if", 2.2, pos, {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [{
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.wf_id}/{name}/cond")),
                    "leftValue": "={{ $json.skip === true }}",
                    "rightValue": "",
                    "operator": {"type": "boolean", "operation": "false", "singleValue": True},
                }],
                "combinator": "and",
            },
            "options": {},
        })

    def sticky(self, text, pos, width=380, height=260, color=7):
        idx = sum(1 for n in self.nodes if n["type"] == "n8n-nodes-base.stickyNote")
        return self.node(f"Note {idx + 1}", "n8n-nodes-base.stickyNote", 1, pos,
                         {"content": text, "width": width, "height": height, "color": color})

    def save(self, file_name):
        wf = {
            "id": self.wf_id,
            "versionId": str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(self.nodes, sort_keys=True))),
            "name": self.name,
            "nodes": self.nodes,
            "connections": self.connections,
            "settings": {"executionOrder": "v1", "timezone": "Asia/Kolkata", "saveManualExecutions": True},
            "active": False,
            "pinData": {},
            "meta": {"templateCredsSetupCompleted": True},
            "tags": [],
        }
        OUT.mkdir(exist_ok=True)
        (OUT / file_name).write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {OUT / file_name} ({len(self.nodes)} nodes)")


# =============================================================================
# Workflow A - Loan Product Discovery (crawler)
# =============================================================================
def build_a():
    w = Workflow("LoanEngineWfA0001", "A · Loan Product Discovery (daily crawler)", "A_discovery")
    w.sticky("## Workflow A · Loan product discovery\n"
             "Daily crawl of **BankBazaar** (rate table + lender pages) and **ICICI Bank** (official page).\n\n"
             "1. Fetch HTML politely (1 req/s, retries)\n2. Strip to ~3 KB eligibility snippet + regex heuristics\n"
             "3. Hash snippet → **skip the LLM if the page is unchanged**\n4. LLM extracts structured criteria (JSON)\n"
             "5. Merge sources (official > aggregator; LLM > cache > regex) → one bulk upsert",
             (-60, -320), width=560, height=280, color=4)
    sched = w.node("Daily 03:00 IST", "n8n-nodes-base.scheduleTrigger", 1.2, (0, 0),
                   {"rule": {"interval": [{"field": "cronExpression", "expression": "0 3 * * *"}]}})
    manual = w.node("Run manually", "n8n-nodes-base.manualTrigger", 1, (0, 200), {})
    # lets the dashboard's "Run Workflow A now" button start a crawl (same header auth as B and C)
    on_demand = w.webhook("Run via dashboard (webhook)", "crawl-products", (0, 400))
    targets = w.code("Crawl targets", "01_crawl_targets.js", (240, 100))
    fetch = w.node("Fetch page", "n8n-nodes-base.httpRequest", 4.2, (480, 100), {
        "url": "={{ $json.url }}",
        "sendHeaders": True,
        "headerParameters": {"parameters": [
            {"name": "User-Agent", "value": "Mozilla/5.0 (compatible; LoanEligibilityEngine/1.0; educational project)"},
            {"name": "Accept-Language", "value": "en-IN,en;q=0.9"}]},
        "options": {
            "response": {"response": {"responseFormat": "text", "outputPropertyName": "html"}},
            "timeout": 20000,
            "batching": {"batch": {"batchSize": 1, "batchInterval": 1000}},
        },
    }, onError="continueRegularOutput", retryOnFail=True, maxTries=2, waitBetweenTries=3000)
    clean = w.code("Clean, parse & fingerprint", "02_clean_parse.js", (720, 100))
    cache = w.node("Load crawl cache", "n8n-nodes-base.postgres", 2.6, (960, 100),
                   {"operation": "executeQuery", "query": "SELECT url, content_hash, extracted FROM crawl_cache;",
                    "options": {}}, credentials=CRED_PG, executeOnce=True, alwaysOutputData=True)
    plan = w.code("Plan LLM extraction", "03_plan_llm.js", (1200, 100))
    gate = w.if_llm_needed("Any page changed?", (1440, 100))
    llm = w.http_llm("LLM: extract criteria", (1680, 0))
    parse = w.code("Parse LLM output", "04_parse_llm.js", (1920, 0))
    final = w.code("Merge sources → products", "05_finalize.js", (2160, 100))
    upsert = w.postgres("Upsert loan_products", "06_upsert.sql",
                        ["$json.products_json", "$json.cache_json"], (2400, 100))

    w.connect(sched, targets)
    w.connect(manual, targets)
    w.connect(on_demand, targets)
    w.chain(targets, fetch, clean, cache, plan, gate)
    w.connect(gate, llm, 0)
    w.chain(llm, parse, final)
    w.connect(gate, final, 1)
    w.connect(final, upsert)
    w.save("A_loan_product_discovery.json")


# =============================================================================
# Workflow B - User-Loan Matching (3-stage optimised pipeline)
# =============================================================================
def build_b():
    w = Workflow("LoanEngineWfB0001", "B · User-Loan Matching (3-stage funnel)", "B_matching")
    w.sticky("## Workflow B · Optimised matching funnel\n"
             "**Stage 1 – SQL:** hard criteria as JOIN predicates in Postgres (cross-product never leaves the DB)\n\n"
             "**Stage 2 – JS:** score, FOIR affordability, keep top-K per user, split clear vs borderline\n\n"
             "**Stage 3 – LLM:** only borderline pairs, collapsed into profile buckets, cached across runs, "
             "25 questions per call, hard call budget\n\nFunnel numbers are saved to `upload_batches.match_stats`.",
             (-60, -360), width=620, height=300, color=4)
    hook = w.webhook("Batch ingested (webhook)", "loan-matching", (0, 100))
    validate = w.code("Validate payload", "01_validate.js", (220, 100))
    mark = w.node("Mark batch: matching", "n8n-nodes-base.postgres", 2.6, (440, 100), {
        "operation": "executeQuery",
        "query": "UPDATE upload_batches SET status = 'matching', updated_at = now() "
                 "WHERE batch_id = $1::uuid RETURNING batch_id::text;",
        "options": {"queryReplacement": "={{ $json.batch_id }}"}}, credentials=CRED_PG)
    s1 = w.postgres("Stage 1 · SQL pre-filter", "02_stage1_prefilter.sql", ["$json.batch_id"], (660, 100))
    s2 = w.code("Stage 2 · Score, top-K, bucket", "03_stage2_score.js", (880, 100))
    lookup = w.postgres("LLM cache lookup", "04_cache_lookup.sql", ["$json.question_keys_json"], (1100, 100),
                        alwaysOutputData=True)
    plan = w.code("Plan LLM batches", "05_plan_llm.js", (1320, 100))
    gate = w.if_llm_needed("LLM work needed?", (1540, 100))
    llm = w.http_llm("Stage 3 · LLM borderline review", (1760, 0))
    parse = w.code("Parse LLM verdicts", "06_parse_llm.js", (1980, 0))
    save_cache = w.postgres("Save verdicts to cache", "07_save_cache.sql", ["$json.rows_json"], (2200, 0),
                            alwaysOutputData=True, executeOnce=True)
    assemble = w.code("Assemble final matches", "08_assemble.js", (2420, 100), executeOnce=True)
    save = w.postgres("Save matches + funnel stats", "09_save_matches.sql",
                      ["$json.batch_id", "$json.matches_json", "$json.stats_json"], (2640, 100))
    notify = w.node("Trigger Workflow C", "n8n-nodes-base.httpRequest", 4.2, (2860, 100), {
        "method": "POST",
        "url": "http://localhost:5678/webhook/notify-users",
        "sendHeaders": True,
        "headerParameters": {"parameters": [{"name": "X-Webhook-Token", "value": "={{ $env.N8N_WEBHOOK_TOKEN }}"}]},
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ batch_id: $json.batch_id }) }}",
        "options": {},
    })

    w.chain(hook, validate, mark, s1, s2, lookup, plan, gate)
    w.connect(gate, llm, 0)
    w.chain(llm, parse, save_cache, assemble)
    w.connect(gate, assemble, 1)
    w.chain(assemble, save, notify)
    w.save("B_user_loan_matching.json")


# =============================================================================
# Workflow C - User Notification
# =============================================================================
def build_c():
    w = Workflow("LoanEngineWfC0001", "C · User Notification (AWS SES)", "C_notification")
    w.sticky("## Workflow C · Notifications\nTriggered by Workflow B when matching finishes.\n\n"
             "* One email per user, all their matches in one table\n"
             "* Recipients are **always rewritten to TEST_INBOX_ALIASES** (never the CSV address)\n"
             "* Sent with the **AWS SES node** (default). `EMAIL_USE_GMAIL=true` switches to a Gmail SMTP fallback\n"
             "* `NOTIFY_MAX_EMAILS` caps a run · only accepted emails are marked notified",
             (-60, -340), width=600, height=280, color=4)
    hook = w.webhook("Matching finished (webhook)", "notify-users", (0, 100))
    fetch = w.postgres("Fetch users with new matches", "01_fetch_pending.sql",
                       ["$json.body.batch_id || 'any'", "$env.NOTIFY_MAX_EMAILS || 3"], (240, 100))
    build = w.code("Build personalised emails", "02_build_email.js", (480, 100))
    toggle = w.node("Send via AWS SES?", "n8n-nodes-base.if", 2.2, (720, 100), {
        "conditions": {
            "options": {"caseSensitive": False, "leftValue": "", "typeValidation": "loose", "version": 2},
            "conditions": [{
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "LoanEngineWfC0001/provider-toggle")),
                # SES unless EMAIL_USE_GMAIL=true in .env (defaults to false)
                "leftValue": "={{ String($env.EMAIL_USE_GMAIL ?? 'false').toLowerCase() !== 'true' }}",
                "rightValue": "",
                "operator": {"type": "boolean", "operation": "true", "singleValue": True},
            }],
            "combinator": "and",
        },
        "options": {},
    })
    gmail = w.node("Send via Gmail SMTP", "n8n-nodes-base.emailSend", 2.1, (960, 200), {
        "fromEmail": "={{ $env.EMAIL_FROM_NAME || 'Loan Eligibility Engine' }} <{{ $env.GMAIL_SMTP_USER }}>",
        "toEmail": "={{ $json.to }}",
        "subject": "={{ $json.subject }}",
        "emailFormat": "html",
        "html": "={{ $json.html }}",
        "options": {"appendAttribution": False},
    }, credentials=CRED_SMTP, onError="continueRegularOutput")
    ses = w.node("Send via AWS SES", "n8n-nodes-base.awsSes", 1, (960, 0), {
        "resource": "email",
        "operation": "send",
        "isBodyHtml": True,
        "subject": "={{ $json.subject }}",
        "body": "={{ $json.html }}",
        "fromEmail": "={{ $env.SES_FROM_EMAIL }}",
        "toAddresses": ["={{ $json.to }}"],
        "additionalFields": {},
    }, credentials=CRED_AWS, onError="continueRegularOutput")
    collect = w.code("Collect send results", "03_collect_results.js", (1200, 100))
    mark = w.postgres("Mark matches notified", "04_mark_notified.sql",
                      ["$json.sent_json", "$json.batch_id || 'none'", "JSON.stringify($json.first_error)"], (1440, 100))
    w.chain(hook, fetch, build, toggle)
    w.connect(toggle, ses, 0)
    w.connect(toggle, gmail, 1)
    w.connect(gmail, collect)
    w.connect(ses, collect)
    w.connect(collect, mark)
    w.save("C_user_notification.json")


if __name__ == "__main__":
    build_a()
    build_b()
    build_c()
