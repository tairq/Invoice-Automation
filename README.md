# 📄 Invoice Processing Automation System

[![CI](https://github.com/tairq/Invoice-Automation/actions/workflows/ci.yml/badge.svg)](https://github.com/tairq/Invoice-Automation/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.12-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

AI-powered invoice processing that handles **any kind of invoice** automatically — PDFs, scanned images, digital invoices — extracting structured data with Claude AI and processing them through a complete pipeline from ingestion to export.

## ✨ Features

- **🤖 AI-Powered Extraction** — Uses Claude Vision API to extract invoice data from any layout without training
- **📎 Multi-Format Support** — PDF, JPG, PNG, TIFF, email attachments (.eml)
- **📤 Multiple Ingestion Sources** — Manual upload via API, email monitoring (IMAP), folder watch
- **🔑 API Key Authentication** — Per-client API keys with SHA-256 hashing and Redis rate limiting
- **✅ Approval Workflow** — Token-based email approval with approve/reject links and audit trail; auto-approve below configurable threshold
- **📋 PO / 3-Way Matching** — Match invoices against purchase orders via PO number or fuzzy vendor name; detect line-item discrepancies
- **🏢 Vendor Master Matching** — Fuzzy-match extracted vendor names against a curated vendor master list with alias support (rapidfuzz, ≥80%)
- **📅 Payment Due Date Tracking** — Parse payment terms (Net 30, 2/10 Net 30, etc.), track due dates, auto-detect overdue invoices, send reminders
- **🎯 Webhook Reliability** — 5 retry attempts with exponential backoff, dead-letter queue on failure, admin email alert
- **📊 Xero Integration** — OAuth PKCE flow, auto-push fully processed invoices to Xero
- **⚡ n8n Integration** — Trigger external n8n workflows for Slack notifications, Airtable updates, and confirmation emails
- **📈 Spend Analytics** — Monthly spending trends, top vendors, currency breakdown via `/api/v1/invoices/stats`
- **📊 Airtable Sync** — Push invoice data directly to Airtable tables
- **📥 CSV/JSON Export** — Download processed data for accounting integration
- **🐳 Docker Ready** — Full containerized deployment with Docker Compose

## 💡 Why This Project

Finance teams waste thousands of hours manually keying invoice data into ERP systems, with error rates that compound across AP workflows. This system replaces that with a single upload (or email forward) that goes from raw document to structured, validated, and synced data — ready for payment, audit, or export. Built with async Python and Celery, it scales from a single-tenant deployment to a multi-org service without re-architecture.

## 🏗️ Architecture

```mermaid
flowchart LR
    subgraph Ingestion["📥 Ingestion"]
        A1[Upload API] --> A2[Validate & Dedup]
        A3[Email Monitor] --> A2
    end

    subgraph Processing["⚙️ Processing Pipeline"]
        B[Preprocess<br/>PDF→Images / OCR] --> C[AI Extraction<br/>Claude Vision]
        C --> D[Validation<br/>Math + Confidence + Dup]
        D --> E{Confidence<br/>≥ 0.85?}
        E -->|Yes| F[Save to DB<br/>extracted_data + line_items]
        E -->|No| G[Flag for Review]
        F --> H[Vendor Matching<br/>Fuzzy Match ≥80%]
        H --> I[PO / 3-Way Matching<br/>Discrepancy Detection]
        I --> J[Payment Terms<br/>Parsing + Due Date]
        J --> K[Approval Workflow<br/>Token-based Email]
    end

    subgraph Output["📬 Output & Sync"]
        L[Webhook Delivery<br/>5 Retries + Dead Letter]
        M[Airtable Sync]
        N[n8n Integration<br/>Slack + Airtable + Email]
        O[Xero Sync<br/>OAuth PKCE]
    end

    Ingestion --> Processing
    Processing --> Output

    style E fill:#f9f,stroke:#333,stroke-width:2px
    style G fill:#f96,stroke:#333,stroke-width:2px
```

## 🚀 Quick Start

### Prerequisites
- Python 3.12+
- Docker & Docker Compose (for containerized setup)
- An Anthropic API key (for AI extraction)

### Local Development

```bash
# Clone and install
cd invoice-processor
pip install -e ".[dev]"

# Copy environment config
cp .env.example .env
# Edit .env — set your ANTHROPIC_API_KEY

# Start dependencies (Postgres + Redis)
docker compose up postgres redis -d

# Run database migrations
alembic upgrade head

# Start the API
uvicorn app.main:app --reload

# In another terminal, start the worker
celery -A app.workers.invoice_worker.celery_app worker --loglevel=info

```

### Docker (Full Stack)

```bash
# Copy and configure
cp .env.example .env
# Edit .env — set your ANTHROPIC_API_KEY

# Start everything
docker compose up -d

# Check health
curl http://localhost:8000/health
```

## 🚢 Deployment (Railway.app)

This project can be deployed on Railway.app with minimal configuration.

### Steps

1. **Push to GitHub** — Ensure your repository is hosted on GitHub
2. **Create a Railway project** — From the Railway dashboard, click "New Project" → "Deploy from GitHub repo"
3. **Select your repo** — Choose `Invoice-Automation` (or your fork)
4. **Set environment variables** — In the Railway dashboard → your project → Variables tab, add:

   | Variable | Value |
   |----------|-------|
   | `DATABASE_URL` | Railway-provided PostgreSQL connection string (Railway auto-provisions this) |
   | `REDIS_URL` | Railway-provided Redis connection string (add Redis plugin first) |
   | `ANTHROPIC_API_KEY` | Your Claude API key |
   | `ADMIN_API_KEY` | A strong random string for API authentication |
   | `SECRET_KEY` | A strong random string |
   | `STORAGE_BACKEND` | `local` (or configure S3) |
   | `N8N_ENABLED` | `false` (set to `true` if using n8n) |

5. **Add plugins** — From Railway dashboard:
   - Add **PostgreSQL** plugin (version 15+)
   - Add **Redis** plugin (version 7+)

6. **Configure start commands** — Railway uses the `Dockerfile` and `docker-compose.yml` in your repo. If using Docker Compose, set the deploy mode to "Docker Compose" in Railway settings. Railway will automatically:
   - Build the Docker image from your Dockerfile
   - Run database migrations (`alembic upgrade head` runs on startup)
   - Start the API server and Celery worker containers

7. **Deploy** — Railway auto-deploys when you push to your default branch

### Health Check

Once deployed, verify the service:

```bash
curl https://your-app-name.railway.app/health
```

## 📚 API Reference

### Invoices

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/invoices/upload` | Upload a single invoice |
| `POST` | `/api/v1/invoices/upload/batch` | Upload multiple invoices |
| `GET` | `/api/v1/invoices` | List invoices (filter, sort, paginate) |
| `GET` | `/api/v1/invoices/stats` | Dashboard stats + spend analytics (monthly trends, top vendors, currency breakdown) |
| `GET` | `/api/v1/invoices/{id}` | Get invoice detail + extracted data |
| `PATCH` | `/api/v1/invoices/{id}/review` | Human review — correct extracted fields |
| `POST` | `/api/v1/invoices/{id}/reprocess` | Re-run full processing pipeline |
| `DELETE` | `/api/v1/invoices/{id}` | Delete invoice + stored file |
| `GET` | `/api/v1/invoices/{id}/log` | Processing audit log |

### Exports

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/exports/csv` | Export invoices as CSV |
| `GET` | `/api/v1/exports/json` | Export invoices as JSON |

### Purchase Orders

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/purchase-orders` | Create purchase order |
| `GET` | `/api/v1/purchase-orders` | List purchase orders |
| `GET` | `/api/v1/purchase-orders/{id}` | Get purchase order |

### Vendors

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/vendors` | Create vendor master record |
| `GET` | `/api/v1/vendors` | List vendors |
| `GET` | `/api/v1/vendors/{id}` | Get vendor |
| `PATCH` | `/api/v1/vendors/{id}` | Update vendor |

### Approvals

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/approvals/{token}/approve` | Approve invoice via email link (no auth required) |
| `GET` | `/api/v1/approvals/{token}/reject` | Reject invoice via email link (no auth required) |

### Webhooks

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/settings/webhook` | Get webhook configuration |
| `POST` | `/api/v1/settings/webhook` | Configure webhook URL and events |
| `GET` | `/api/v1/settings/webhook/deliveries` | Webhook delivery history |

### Integrations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/integrations/xero/connect` | Start Xero OAuth PKCE flow (returns auth URL) |
| `POST` | `/api/v1/integrations/xero/callback` | Submit Xero auth code + PKCE verifier |
| `GET` | `/api/v1/integrations/xero/status` | Check Xero connection status |
| `POST` | `/api/v1/n8n/trigger` | Trigger n8n workflow externally |

### Admin

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/admin/keys` | Create a new API key (admin) |
| `GET` | `/api/v1/admin/keys` | List all API keys (admin) |
| `PATCH` | `/api/v1/admin/keys/{id}/deactivate` | Deactivate an API key (admin) |

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |

## 📊 Airtable Sync

Processed invoice data is automatically pushed to Airtable tables right after extraction. This replaces the traditional dashboard with a live, shareable view of your data.

### Tables

| Table | Description |
|-------|-------------|
| **Invoices** | One record per invoice — vendor, customer, totals, status |
| **Line Items** | Individual line items linked by Invoice ID |

### Setup

1. Create an Airtable base with **Invoices** and **Line Items** tables
2. Add the corresponding fields (the service auto-maps all extraction fields)
3. Configure in `.env`:

```env
AIRTABLE_API_KEY=pat-your-api-key
AIRTABLE_BASE_ID=appYourBaseId
AIRTABLE_SYNC_ENABLED=true
```

## 💳 Xero Integration

The system can automatically push fully processed invoices to Xero for accounting integration.

### How It Works

Xero integration uses the OAuth PKCE (Proof Key for Code Exchange) flow, designed for Xero's Desktop app type. The user connects their Xero organisation once via a browser-based authorization flow, and tokens are automatically refreshed without further interaction. This means no client secret is needed — only a Xero client ID.

Once connected, every invoice that completes the full processing pipeline (status `done`) is pushed to the connected Xero organisation with the following data:

| Field | Maps To |
|-------|---------|
| Invoice number | `invoice_id` / system-generated number |
| Vendor | Contact (created or matched) |
| Line items | LineItem objects with description, quantity, unit amount, tax |
| Subtotal, tax, total | Invoice totals |
| Currency | Invoice currency (ISO code) |
| Due date | Due date parsed from payment terms |

### Setup

```env
XERO_CLIENT_ID=your-xero-client-id
XERO_ENABLED=true
XERO_REDIRECT_URI=https://your-app.com/api/v1/integrations/xero/callback
```

The connection flow:

1. Navigate to `/api/v1/integrations/xero/connect` — returns a Xero authorization URL
2. Authorize in your browser and copy the authorization code
3. Submit the code to `/api/v1/integrations/xero/callback` with the PKCE verifier
4. Verify connection at `/api/v1/integrations/xero/status`
5. Subsequent processed invoices push to Xero automatically

## 🎯 n8n Integration

The system can automatically trigger an n8n workflow at the end of every successful invoice pipeline. This enables no-code automation for downstream processes like Slack notifications, Airtable updates, and confirmation emails.

### Flow

```
Invoice processed → Webhook fires → n8n receives payload → Routes to:
  ├── Slack (team notification)
  ├── Airtable (upsert invoice record)
  └── Email (send confirmation to vendor)
```

### Payload

The webhook sends the following payload to the configured n8n endpoint:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Invoice UUID |
| `status` | string | Processing status |
| `vendor_name` | string | Extracted vendor name |
| `total_amount` | number | Invoice total |
| `currency` | string | ISO currency code (e.g. USD, EUR) |
| `due_date` | string | Payment due date (YYYY-MM-DD) |
| `line_items` | array | Line item objects with description, quantity, unit price |

### Setup

1. Deploy n8n (self-host or n8n.cloud)
2. Import [`docs/n8n-starter-workflow.json`](docs/n8n-starter-workflow.json) into n8n
3. Configure in `.env`:

```env
N8N_WEBHOOK_URL=https://your-n8n-instance.example.com/webhook/invoice-processed
N8N_ENABLED=true
```

The starter workflow includes:

| Node | Purpose |
|------|---------|
| **Webhook Trigger** | Receives the invoice payload from this system |
| **Send Slack Message** | Posts invoice details (vendor, amount, status) to a channel |
| **Update Airtable** | Upserts the invoice record into Airtable |

You can extend the workflow by adding nodes for email (SMTP), Google Sheets, accounting APIs, or any of n8n's 400+ integrations.

### API Endpoint

You can also trigger the n8n workflow externally:

```bash
curl -X POST http://localhost:8000/api/v1/n8n/trigger \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"id": "abc-123", "vendor_name": "Acme Corp", ...}'
```

## 🔧 Configuration

Key environment variables (see `.env.example` for all options):

### Core

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string (async) | `postgresql+asyncpg://postgres:postgres@localhost:5432/invoice_processor` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `SECRET_KEY` | Application secret key (used for token signing) | — |

### LLM / AI Extraction

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key | — |
| `ANTHROPIC_MODEL` | Claude model for extraction | `claude-sonnet-5-20250601` |
| `LLM_PROVIDER` | LLM provider (anthropic, openai, custom) | `anthropic` |

### Processing

| Variable | Description | Default |
|----------|-------------|---------|
| `CONFIDENCE_THRESHOLD` | Minimum confidence score to auto-pass | `0.85` |
| `MAX_FILE_SIZE_MB` | Maximum uploaded file size in MB | `10` |
| `ALLOWED_EXTENSIONS` | Comma-separated list of allowed file extensions | `.pdf,.jpg,.jpeg,.png,.tiff,.tif,.eml` |

### Storage

| Variable | Description | Default |
|----------|-------------|---------|
| `STORAGE_BACKEND` | Storage backend (`local` or `s3`) | `local` |
| `STORAGE_PATH` | Path for local file storage | `./uploads` |

### Email Monitoring (IMAP)

| Variable | Description | Default |
|----------|-------------|---------|
| `EMAIL_HOST` | IMAP server hostname | — |
| `EMAIL_USERNAME` | IMAP username | — |
| `EMAIL_PASSWORD` | IMAP password | — |

### SMTP (Outgoing Email)

| Variable | Description | Default |
|----------|-------------|---------|
| `SMTP_HOST` | SMTP server hostname | — |
| `SMTP_PORT` | SMTP server port | `587` |
| `SMTP_USER` | SMTP username | — |
| `SMTP_PASS` | SMTP password | — |

### Approval Workflow

| Variable | Description | Default |
|----------|-------------|---------|
| `APPROVAL_THRESHOLD` | Minimum total to require approval (`0` = all invoices) | `0` |
| `APPROVAL_BASE_URL` | Base URL for approval links in emails | `http://localhost:8000` |
| `APPROVAL_RECIPIENT_EMAIL` | Default recipient for approval emails | — |

### Airtable

| Variable | Description | Default |
|----------|-------------|---------|
| `AIRTABLE_API_KEY` | Airtable personal access token | — |
| `AIRTABLE_BASE_ID` | Airtable base ID | — |
| `AIRTABLE_SYNC_ENABLED` | Enable automatic Airtable sync | `false` |

### Xero

| Variable | Description | Default |
|----------|-------------|---------|
| `XERO_CLIENT_ID` | Xero OAuth2 client ID | — |
| `XERO_CLIENT_SECRET` | Xero OAuth2 client secret | — |
| `XERO_ENABLED` | Enable Xero integration | `false` |
| `XERO_REDIRECT_URI` | OAuth redirect URI for Xero callback | — |

### n8n

| Variable | Description | Default |
|----------|-------------|---------|
| `N8N_WEBHOOK_URL` | n8n webhook URL for workflow trigger | — |
| `N8N_ENABLED` | Enable automatic n8n trigger after pipeline | `false` |

### Admin & Security

| Variable | Description | Default |
|----------|-------------|---------|
| `ADMIN_API_KEY` | Admin API key for admin endpoints (X-Admin-Key header) | — |

## 🧪 Testing

```bash
# Run all tests
pytest

# With coverage
pytest --cov=app --cov-report=term

# Run specific test file
pytest tests/test_api.py -v
```

Test patterns: SQLite via aiosqlite, Celery `.delay()` mocked, API key auth bypassed via `app.dependency_overrides`, database recreated per test.

## 📁 Project Structure

```
invoice-processor/
├── app/
│   ├── main.py                 # FastAPI app entry point, lifespan, exception handlers, health check
│   ├── config.py               # Settings (pydantic-settings): .env → Settings singleton
│   ├── database.py             # Async SQLAlchemy engine, session factory, Base, get_session
│   ├── models/                 # 13 SQLAlchemy models
│   │   ├── invoice.py          # Central entity — status, source, approval/PO/payment status
│   │   ├── extracted_data.py   # ~40 extraction fields (1:1 with Invoice)
│   │   ├── line_item.py        # Line items (1:N with Invoice): description, qty, price, tax
│   │   ├── extraction_confidence.py  # Per-field confidence + human corrections
│   │   ├── processing_log.py   # Audit trail — step, status, message, duration_ms
│   │   ├── organization.py     # Multi-tenant: name, email_config, webhook_url, settings
│   │   ├── api_key.py          # API key: key_hash, rate_limit, last_used_at, is_active
│   │   ├── approval_token.py   # Token-based approval: token, email, redeemed_at
│   │   ├── purchase_order.py   # PO master: po_number, vendor_name, line_items (JSON)
│   │   ├── po_match.py         # Match results + line-item discrepancies
│   │   ├── vendor.py           # Vendor master list with aliases (JSON)
│   │   ├── webhook_delivery.py # Delivery attempts: status, response_code, attempt_number
│   │   └── xero_credential.py  # Encrypted OAuth tokens, tenant_id, organization_id
│   ├── schemas/                # Pydantic request/response models
│   ├── routers/                # FastAPI route handlers
│   │   ├── invoices.py         # CRUD + upload + review + reprocess + stats
│   │   ├── exports.py          # CSV/JSON export
│   │   ├── webhooks.py         # Webhook config + delivery history
│   │   ├── approvals.py        # Approval tokens + purchase orders + vendors
│   │   ├── integrations.py     # Xero OAuth PKCE flow
│   │   ├── n8n.py              # n8n workflow trigger
│   │   └── admin.py            # API key management
│   ├── services/               # Business logic
│   │   ├── ingestion.py        # File validation, storage, dedup
│   │   ├── preprocessor.py     # PDF→images, OCR fallback
│   │   ├── extractor.py        # AI-powered extraction (Claude Vision with structured schema)
│   │   ├── validator.py        # Cross-field math checks, confidence scoring, dup detection
│   │   ├── exporter.py         # CSV/JSON generation
│   │   ├── email_monitor.py    # IMAP inbox watching
│   │   ├── approval.py         # Token generation, approval emails, redemption
│   │   ├── po_matching.py      # PO matching + line item discrepancy detection
│   │   ├── vendor_matching.py  # Fuzzy vendor name matching (rapidfuzz, ≥80%)
│   │   ├── payment_terms.py    # Payment terms parsing (regex + LLM)
│   │   ├── airtable_sync.py    # Push to Airtable tables
│   │   ├── xero_sync.py        # Push invoices to Xero (async token mgmt + OAuth)
│   │   └── xero_client.py      # xero-python SDK wrapper (sync, AccountingApi)
│   ├── core/                   # Infrastructure
│   │   ├── storage.py          # LocalStorage / S3Storage singleton
│   │   ├── ocr.py              # Tesseract OCR wrapper (fallback)
│   │   ├── llm_client.py       # Claude API client (also supports OpenAI/custom)
│   │   ├── auth.py             # API key auth + Redis rate limiting
│   │   └── redis.py            # Redis connection helper
│   ├── workers/
│   │   └── invoice_worker.py   # Celery: pipeline + beat schedule + webhook delivery
│   └── migrations/             # Alembic (env.py, script.py.mako, versions/)
├── tests/                      # Pytest test suite (12 test files)
├── .github/workflows/
│   └── ci.yml                  # GitHub Actions: lint, test (Postgres + Redis), docker build
├── docker-compose.yml          # Full stack orchestration
├── Dockerfile                  # Container build
└── pyproject.toml              # Project config & dependencies
```

## 🔄 Processing Pipeline

The full 13-step pipeline runs asynchronously in Celery workers. Steps 6–13 are non-fatal — individual failures log warnings without failing the invoice.

1. **Ingestion** — File validated for type and size, deduplicated by hash, stored, and database record created
2. **Preprocessing** — PDF converted to PNG images; Tesseract OCR used as fallback for unreadable documents
3. **AI Extraction** — Claude Vision (or configured LLM) analyzes images with a structured extraction schema; returns ~30 header fields + line items + per-field confidence
4. **Validation** — Math cross-checks (subtotal + tax - discount ≈ grand_total), confidence scoring, duplicate detection
5. **Save** — Extracted data, line items, and confidence scores written to PostgreSQL
6. **Vendor Matching** — Fuzzy-match extracted vendor name against vendor master list (rapidfuzz, ≥80% threshold)
7. **PO / 3-Way Matching** — Match invoice against purchase orders by PO number or vendor; flag line-item quantity and price discrepancies
8. **Payment Terms Parsing** — Parse "Net 30", "2/10 Net 30" via regex + LLM; set due_date and payment_status
9. **Approval Workflow** — Generate approval token; send email with approve/reject links if above threshold (or if all invoices require approval)
10. **Webhook Delivery** — Fire configured webhook via Celery task with retry (5 attempts, exponential backoff, dead-letter queue, admin email alert on permanent failure)
11. **Airtable Sync** — Push invoice and line item data to Airtable tables
12. **n8n Integration** — Trigger external n8n workflow (Slack + Airtable + email) if enabled
13. **Xero Sync** — Push fully processed invoices to connected Xero organisation (OAuth PKCE, auto-refresh tokens)

Invoices with overall confidence below 0.85 are set to `needs_review` status and queued for manual correction via the review endpoint.

## 🛣️ Roadmap

### Completed
- [x] Core extraction pipeline (upload → AI → validation → storage)
- [x] Multi-format support (PDF, images, TIFF, EML)
- [x] REST API with full CRUD
- [x] Async processing with Celery
- [x] Airtable sync
- [x] Email monitoring (IMAP)
- [x] CSV/JSON export
- [x] Docker deployment
- [x] Authentication & API keys
- [x] Xero integration (OAuth PKCE, auto-push)
- [x] Approval workflow (email-based, token auth)
- [x] PO / 3-way matching (discrepancy detection)
- [x] Vendor master matching (fuzzy name matching)
- [x] Payment due date tracking + reminders
- [x] Webhook retry + dead-letter queue
- [x] n8n integration
- [x] Spend analytics endpoint
- [x] CI/CD pipeline (GitHub Actions)

### In Progress / Planned
- [ ] S3 storage backend
- [ ] QuickBooks integration
- [ ] Multi-language invoice support
- [ ] React dashboard frontend
- [ ] Performance benchmark suite

## 📄 License

MIT
