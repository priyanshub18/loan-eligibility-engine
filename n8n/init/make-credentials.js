// Emits n8n credential definitions (decrypted form) built from env vars.
// `n8n import:credentials` encrypts them with N8N_ENCRYPTION_KEY on import,
// so no secret is ever committed to the repo or baked into workflow JSON.
const env = process.env;

const credentials = [
  {
    id: 'LoanEnginePgDb01',
    name: 'Loan Engine Postgres',
    type: 'postgres',
    data: {
      host: env.N8N_DB_HOST,
      port: Number(env.N8N_DB_PORT || 5432),
      database: env.N8N_DB_NAME,
      user: env.N8N_DB_USER,
      password: env.N8N_DB_PASSWORD,
      ssl: env.N8N_DB_SSL || 'disable',          // 'require' for RDS
      allowUnauthorizedCerts: env.N8N_DB_SSL === 'require',
      maxConnections: 10,
    },
  },
  {
    id: 'LoanEngineHookAuth',
    name: 'Loan Engine Webhook Token',
    type: 'httpHeaderAuth',
    data: { name: 'X-Webhook-Token', value: env.N8N_WEBHOOK_TOKEN },
  },
  {
    id: 'LoanEngineAwsSes1',
    name: 'Loan Engine AWS (SES)',
    type: 'aws',
    data: {
      region: env.AWS_REGION || 'ap-south-1',
      accessKeyId: env.AWS_ACCESS_KEY_ID || '',
      secretAccessKey: env.AWS_SECRET_ACCESS_KEY || '',
      temporaryCredentials: false,
    },
  },
  {
    // Gmail SMTP with an App Password (Google Account -> Security -> App passwords)
    id: 'LoanEngineGmailSmtp',
    name: 'Loan Engine Gmail SMTP',
    type: 'smtp',
    data: {
      user: env.GMAIL_SMTP_USER || '',
      password: (env.GMAIL_SMTP_APP_PASSWORD || '').replace(/\s+/g, ''), // Google shows it as "abcd efgh ijkl mnop"
      host: 'smtp.gmail.com',
      port: 465,
      secure: true,
    },
  },
];

process.stdout.write(JSON.stringify(credentials));
