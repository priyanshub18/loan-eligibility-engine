// Activates every imported workflow.
//
// Why not `n8n update:workflow --all --active=true`? In n8n 1.12x activation
// points workflow_entity.activeVersionId at a workflow_history row, but
// `import:workflow` doesn't create one, so the CLI fails on a foreign key.
// We create the missing history snapshot (exactly what the editor's
// "Save" would do) and then flag the workflow active, before n8n starts.
const sqlite3 = require(process.env.SQLITE3_MODULE);
const db = new sqlite3.Database('/home/node/.n8n/database.sqlite');

const run = sql => new Promise((resolve, reject) =>
  db.run(sql, function (err) { err ? reject(err) : resolve(this.changes); }));

(async () => {
  await run(`INSERT OR IGNORE INTO workflow_history (versionId, workflowId, authors, nodes, connections, name)
             SELECT versionId, id, 'n8n-init', nodes, connections, name FROM workflow_entity`);
  const n = await run(`UPDATE workflow_entity SET active = 1, activeVersionId = versionId`);
  console.log(`n8n-init: activated ${n} workflow(s)`);

  // Public-API key for the dashboard (read workflows + executions). Keys with
  // the legacy "n8n_api_" prefix are matched by value, so a key from .env works.
  const key = process.env.N8N_API_KEY || '';
  if (key.startsWith('n8n_api_')) {
    const scopes = JSON.stringify(['workflow:read', 'workflow:list', 'execution:read', 'execution:list']);
    const added = await run(`INSERT OR REPLACE INTO user_api_keys (id, userId, label, apiKey, scopes, audience)
             SELECT 'loan-engine-dashboard-key', id, 'loan-engine-dashboard', '${key.replace(/[^A-Za-z0-9_]/g, '')}', '${scopes}', 'public-api'
             FROM user WHERE roleSlug = 'global:owner' LIMIT 1`);
    console.log(`n8n-init: dashboard API key ${added ? 'provisioned' : 'skipped (no owner yet; re-run after creating the owner)'}`);
  }
  db.close();
})().catch(err => { console.error(err); process.exit(1); });
