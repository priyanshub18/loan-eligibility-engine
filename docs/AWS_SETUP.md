# AWS Setup Runbook

How to deploy the AWS stack, connect it to self-hosted n8n, and prove that each requirement works.
Work through it top to bottom. Each phase ends with a **✅ Verify** check. Don't move on until it passes.

Region: **`ap-south-1` (Mumbai)**, used everywhere. SES, the CLI and the deploy must all use the same region.

---

## What gets deployed (and which requirement it covers)

| AWS resource (from `backend/serverless.yml`) | Requirement it covers |
|---|---|
| **API Gateway HTTP API** + **λ uploadUrl** (`POST /uploads`) | The UI asks for a presigned S3 POST, so the file **never passes through API Gateway or Lambda** (avoids the 10 MB / 30 s limits) |
| **S3 bucket** `uploads/` (private, encrypted, 7-day expiry) | The browser uploads the CSV directly here |
| **SQS queue + DLQ** (S3 `ObjectCreated` → SQS) | Event-driven, retried 3 times, then dead-lettered |
| **λ ingestCsv** (SQS trigger, 15 min timeout, concurrency 5) | Parses the CSV and writes it to the `users` table in RDS with `COPY`, then calls the **n8n Workflow B webhook** |
| **λ uploadStatus** (`GET /uploads/{id}`) | The UI shows progress and the matching funnel |
| **λ migrate** | Creates all tables in RDS |
| **RDS PostgreSQL 16** (`db.t4g.micro`, password in **Secrets Manager**) | Stores the data in RDS |
| **SSM SecureString** | The webhook secret the Lambda sends to n8n (never a plain env var) |
| **SES** (used from n8n Workflow C's **AWS SES node**) | Sends the notification email |

---

## Phase 0: Prerequisites (5 min)

```bash
aws --version            # AWS CLI v2
node --version           # 18+
python3 --version        # any 3.x (deps are pure Python)
docker info              # Docker Desktop running
```

Log in to the AWS CLI with **access keys** rather than SSO:

1. Sign in at https://console.aws.amazon.com as the **root user** (the email you signed up with). Don't use the old SSO "start" link.
2. Go to **IAM → Users → Create user**, name it `loan-engine-deployer`, and choose **Attach policies directly → `AdministratorAccess`**. Create the user.
3. Open the user and go to **Security credentials → Create access key → Command Line Interface (CLI)**. Tick the box, then copy the **Access key ID** and **Secret access key**. The secret is only shown once.
4. In the terminal:
```bash
mv ~/.aws/config ~/.aws/config.sso-backup   # disable the expired SSO default profile
aws configure
#   AWS Access Key ID:     <paste>
#   AWS Secret Access Key: <paste>
#   Default region name:   ap-south-1
#   Default output format: json
```

**✅ Verify**
```bash
aws sts get-caller-identity          # prints your account + ARN
aws ec2 describe-vpcs --region ap-south-1 --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId'
```
The second command must print a `vpc-…` id. If it prints `null`, run `aws ec2 create-default-vpc --region ap-south-1`.

---

## Phase 1: SES + IAM user for n8n (10 min, console)

### 1a. Verify email identities in SES
1. Open the AWS console, go to **Amazon SES**, and switch the region (top right) to **Asia Pacific (Mumbai) ap-south-1**.
2. Go to **Configuration → Identities → Create identity → Email address**. Create **three** identities:
   - `yourname@gmail.com` (the sender, `SES_FROM_EMAIL`)
   - `yourname+one@gmail.com`
   - `yourname+two@gmail.com`
3. All three verification emails arrive in your normal Gmail inbox. Click each link.

> Why the aliases too: a new account is in the **SES sandbox**, which only delivers to verified addresses, and every `+alias` counts as a separate address. You don't need production access, because the brief only asks you to send to test aliases.

**✅ Verify:** all three identities show **Verified**. A CLI test send (with your own addresses):
```bash
aws ses send-email --region ap-south-1 \
  --from yourname@gmail.com --destination ToAddresses=yourname+one@gmail.com \
  --message 'Subject={Data=SES test},Body={Text={Data=hello}}'
```
→ returns a `MessageId`, and the mail arrives (check Spam the first time).

### 1b. IAM user that n8n uses for the SES node
1. Go to **IAM → Users → Create user**. Name it `loan-engine-n8n-ses` and give it **no** console access.
2. Choose **Attach policies directly → Create policy → JSON**:
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{ "Effect": "Allow", "Action": ["ses:SendEmail", "ses:SendRawEmail"], "Resource": "*" }]
   }
   ```
   Name the policy `LoanEngineSesSendOnly` and attach it to the user.
3. Open the user and go to **Security credentials → Create access key → "Application running outside AWS"**. Copy both values.

### 1c. Put them in `.env`
```dotenv
EMAIL_USE_GMAIL=false                 # false = AWS SES node (required by the brief)
AWS_REGION=ap-south-1
AWS_ACCESS_KEY_ID=AKIA...             # from 1b
AWS_SECRET_ACCESS_KEY=...             # from 1b
SES_FROM_EMAIL=yourname@gmail.com
TEST_INBOX_ALIASES=yourname+one@gmail.com,yourname+two@gmail.com
NOTIFY_MAX_EMAILS=3
LLM_API_KEY=...                       # free Gemini key from aistudio.google.com, so Stage 3 (LLM) really runs
```

---

## Phase 2: Expose n8n so the Lambda can reach it (5 min)

The ingest Lambda runs in AWS. It has to call the Workflow B webhook on the n8n instance on your laptop. The compose file includes a free Cloudflare tunnel for that.

```bash
cd ~/click-pe-assignment
docker compose up -d                                  # app-db + n8n (first run imports workflows)
docker compose --profile tunnel up -d                 # starts cloudflared
docker compose logs tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1
```

Copy the URL into `.env`:
```dotenv
N8N_PUBLIC_URL=https://<id>.trycloudflare.com/
```
```bash
docker compose up -d --force-recreate n8n-init n8n    # reload credentials (SES keys) + public URL
```

**✅ Verify:** an unauthenticated call must be **rejected**. That proves the webhook is reachable and protected:
```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<id>.trycloudflare.com/webhook/loan-matching
# → 403 (or 401).  A 404 means the workflow isn't active; a timeout means the tunnel is down.
```

> ⚠️ The quick-tunnel URL **changes whenever the tunnel container restarts**. If it changes, repeat Phase 2 and re-run the `deploy` from Phase 3 (about 1 min). Don't restart the tunnel between deploying and recording the video.

---

## Phase 3: Deploy the stack (≈ 12 min, mostly waiting for RDS)

```bash
cd ~/click-pe-assignment
set -a; source .env; set +a                           # puts N8N_WEBHOOK_TOKEN etc. in this shell
cd backend
npm install                                           # Serverless v3 + python-requirements plugin

# 1. webhook secret -> SSM (the Lambda reads it at runtime)
aws ssm put-parameter --region ap-south-1 --overwrite \
  --name /loan-engine/dev/n8n-webhook-token --type SecureString --value "$N8N_WEBHOOK_TOKEN"
# n8n API key -> SSM (the dashboard Lambda reads workflow runs with it)
aws ssm put-parameter --region ap-south-1 --overwrite \
  --name /loan-engine/dev/n8n-api-key --type SecureString --value "$N8N_API_KEY"

# 2. tell the stack where Workflow B lives
export N8N_MATCH_WEBHOOK_URL=https://<id>.trycloudflare.com/webhook/loan-matching

# 3. deploy
npx serverless deploy --stage dev --region ap-south-1

# 4. create tables in RDS
npx serverless invoke -f migrate --stage dev --region ap-south-1

# 5. note the outputs
npx serverless info --stage dev --region ap-south-1 --verbose
```

From the `info` output, write down:
- **endpoints:** `https://<api-id>.execute-api.ap-south-1.amazonaws.com`
- **DbEndpoint:** `loan-eligibility-engine-dev.xxxx.ap-south-1.rds.amazonaws.com`
- **DbSecretArn:** `arn:aws:secretsmanager:…`

**✅ Verify**
```bash
# API is up and creates a batch + presigned POST
curl -s -X POST https://<api-id>.execute-api.ap-south-1.amazonaws.com/uploads \
  -H 'Content-Type: application/json' -d '{"file_name":"users.csv","size":1000}' | head -c 400
# → 201 JSON with "batch_id" and "upload": {"url": "https://<bucket>.s3...", "fields": {...}}
```
In the console, check:
- **CloudFormation:** `loan-eligibility-engine-dev` is `UPDATE_COMPLETE` or `CREATE_COMPLETE`.
- **RDS:** the instance is *Available*.
- **SQS:** both queues exist.
- **S3:** the bucket exists.
- **Lambda:** 4 functions exist.

---

## Phase 4: Point n8n at RDS (5 min)

All of n8n's business logic now has to read from and write to **RDS**, not the local Postgres.

```bash
aws secretsmanager get-secret-value --region ap-south-1 \
  --secret-id <DbSecretArn> --query SecretString --output text
# → {"username":"loanadmin","password":"<RDS_PASSWORD>"}
```

Edit `.env`:
```dotenv
N8N_DB_HOST=<DbEndpoint>
N8N_DB_PORT=5432
N8N_DB_NAME=loanengine
N8N_DB_USER=loanadmin
N8N_DB_PASSWORD=<RDS_PASSWORD>
N8N_DB_SSL=require
```
```bash
docker compose up -d --force-recreate n8n-init n8n
```

This recreates the **"Loan Engine Postgres"** credential in n8n from `.env`. (For the video: you can show the credential under n8n → *Credentials*. The README §3.2 has a table showing how to create it by hand.)

**✅ Verify:** open http://localhost:5678, go to **Credentials → Loan Engine Postgres**, and click **Test**. It should report success.

Optional, to look at RDS from your laptop:
```bash
docker run --rm -it postgres:16-alpine psql \
  "host=<DbEndpoint> port=5432 dbname=loanengine user=loanadmin sslmode=require"
# \dt  → users, loan_products, matches, upload_batches, crawl_cache, llm_decision_cache, notifications
```

---

## Phase 5: Run the whole pipeline on AWS (10 min)

### 5a. Workflow A: fill `loan_products` in RDS
In n8n, open **A · Loan Product Discovery** and click **Execute workflow**.
**✅ Verify:** in psql, `select count(*), min(min_credit_score) from loan_products;` returns a non-zero count (about 14).
The schedule trigger (daily 03:00 IST) is active, so the workflow also runs on its own.

### 5b. Upload the CSV from the UI
1. Edit `frontend/config.js`:
   ```js
   window.API_BASE = 'https://<api-id>.execute-api.ap-south-1.amazonaws.com';
   ```
2. Open `frontend/index.html` in Chrome and drop in `sample-data/users.csv`.
3. Watch the status move through `awaiting_upload → ingesting → ingested → matching → matched → notified`.

### 5c. Prove each hop (do this live in the video)

| Hop | Where to look | What you should see |
|---|---|---|
| Browser → S3 (direct) | S3 console → bucket → `uploads/` | `<batch_id>.csv` |
| S3 → SQS → Lambda | `npx serverless logs -f ingestCsv --stage dev --region ap-south-1` | an ingest log line with rows accepted / rejected, then a webhook 200 |
| Data in RDS | psql: `select count(*) from users;` | ≈ 10,000 |
| Lambda → n8n B | n8n → **Executions** → *B · User-Loan Matching* | a green run; open the Stage 1/2/3 nodes to show the funnel |
| Matches stored | psql: `select count(*), count(distinct user_id) from matches;` | non-zero |
| B → C → SES | n8n → Executions → *C · User Notification* → **Send via AWS SES** node | output contains a `MessageId` |
| Email received | Gmail inbox (`yourname+one@gmail.com`) | personalised email listing the loan products |
| Nothing failed | SQS console → `…-uploads-dlq` | 0 messages |

---

## Phase 6: Requirement sign-off checklist

- [ ] **Bulk CSV upload is event-driven and not limited by API Gateway size:** presigned POST → S3 → SQS → Lambda (5c, rows 1–2)
- [ ] **Data parsed and stored in RDS PostgreSQL:** `users` count ≈ 10k on RDS (5c, row 3)
- [ ] **n8n self-hosted in Docker:** `docker compose ps`
- [ ] **Workflow A runs on a schedule, crawls sites and fills `loan_products`** (5a)
- [ ] **Workflow B triggered by a webhook after ingestion** (Lambda → tunnel → n8n, 5c, row 4)
- [ ] **Multi-stage optimisation:** the B execution shows SQL pre-filter → scoring → a few batched LLM calls
- [ ] **Results in `matches`** (5c, row 5)
- [ ] **Workflow C runs after B and uses the AWS SES node** (5c, row 6)
- [ ] **Email received at a `+alias` test inbox, not a CSV address** (5c, row 7)
- [ ] **Minimal UI starts the pipeline** (5b)
- [ ] `serverless.yml`, `docker-compose.yml` and `n8n/workflows/*.json` are committed to the private repo with the 4 collaborators invited

---

## Troubleshooting

| Symptom | Cause → fix |
|---|---|
| `Token has expired and refresh failed` | `aws sso login` |
| `serverless: command not found` / plugin missing | Run `npm install` inside `backend/`, and always use `npx serverless` |
| `python3.12: not found` during deploy | Already fixed (`pythonBin: python3`). If it still happens: `export PYTHON_BIN=python3` |
| Deploy fails on RDS / security group: *"No default VPC"* | `aws ec2 create-default-vpc --region ap-south-1`, then deploy again |
| Upload stuck at `ingested` / shows `notify_webhook_failed` | The Lambda couldn't reach n8n. Check `serverless logs -f ingestCsv`. The tunnel URL probably changed: redo Phase 2 and deploy again with the new `N8N_MATCH_WEBHOOK_URL` |
| Lambda log shows webhook **403** | The SSM token doesn't match `.env` `N8N_WEBHOOK_TOKEN`. Re-run the `put-parameter` command with `--overwrite` |
| n8n Postgres credential test times out | The RDS security group is open to `0.0.0.0/0` by default. If you set `DB_ALLOWED_CIDR`, it must include your current public IP (`curl ifconfig.me`) |
| SES node: `MessageRejected: Email address is not verified` | The alias or sender isn't verified in **ap-south-1** (Phase 1a), or `AWS_REGION` in `.env` is a different region |
| SES node: `AccessDenied` | The IAM user is missing `ses:SendEmail` (Phase 1b) |
| Email doesn't arrive | Check Gmail **Spam**. Also check that C's run isn't empty: `NOTIFY_MAX_EMAILS` caps each run, and only users with *new* matches are emailed |
| Message in the DLQ | The CSV failed 3 times. `serverless logs -f ingestCsv` shows the error. The UI shows the batch as `failed` |

---

## Cost and teardown

- RDS `db.t4g.micro` with 20 GB gp3 is covered by the free tier or free credits on most accounts. The public IPv4 address costs about $0.005/hour. Lambda, SQS, S3, API Gateway and SES at this volume cost ≈ $0.
- **Tear down after you submit** (or after recording, if the reviewers don't need it live):
  ```bash
  cd backend && npx serverless remove --stage dev --region ap-south-1
  ```
  Backups are disabled and `DeletionPolicy: Delete` means no snapshot is left behind. Also delete the SSM parameter and the IAM user's access key.
