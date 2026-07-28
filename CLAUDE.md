# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Invoice Processing Automation

**Stack:** Python 3.12 | FastAPI | SQLAlchemy 2.0 (async) | Celery + Redis | PostgreSQL | Claude API | Airtable | Xero

## Project Structure

```
app/
├── main.py              # FastAPI app, lifespan, exception handlers, health check
├── config.py            # pydantic-settings: .env → Settings singleton
├── database.py          # async engine, session factory, Base, get_session dependency
├── models/              # 11 SQLAlchemy models (see below)
├── schemas/             # Pydantic request/response models
├── routers/             # FastAPI routers
│   ├── invoices.py      # CRUD + upload + review + reprocess + stats
│   ├── exports.py       # CSV/JSON export
│   ├── webhooks.py      # Webhook config + delivery history
│   ├── approvals.py     # Approval tokens + purchase orders + vendors
│   ├── integrations.py  # Xero OAuth PKCE flow
│   ├── n8n.py           # n8n workflow trigger
│   └── admin.py         # API key management
├── services/            # Business logic
│   ├── ingestion.py     # File validation, storage, dedup
│   ├── preprocessor.py  # PDF→images, OCR fallback
│   ├── extractor.py     # AI-powered extraction
│   ├── validator.py     # Cross-field math checks, confidence scoring, duplicate detection
│   ├── exporter.py      # CSV/JSON generation
│   ├── email_monitor.py # IMAP inbox watching
│   ├── approval.py      # Token generation, approval emails, redemption
│   ├── po_matching.py   # PO matching + line item discrepancy detection
│   ├── vendor_matching.py # Fuzzy vendor name matching (rapidfuzz, ≥80%)
│   ├── payment_terms.py # Payment terms parsing (regex + LLM)
│   ├── airtable_sync.py # Push to Airtable tables
│   ├── xero_sync.py     # Push invoices to Xero (async token mgmt + OAuth)
│   └── xero_client.py   # xero-python SDK wrapper (sync, AccountingApi)
├── core/                # Infrastructure
│   ├── storage.py       # LocalStorage / S3Storage singleton
│   ├── ocr.py           # Tesseract OCR wrapper (fallback)
│   ├── llm_client.py    # Claude API client (also supports OpenAI/custom)
│   ├── auth.py          # API key auth + Redis rate limiting
│   └── redis.py         # Redis connection helper
├── workers/
│   └── invoice_worker.py # Celery: pipeline + beat schedule + webhook delivery
└── migrations/          # Alembic (env.py, script.py.mako, versions/)
```

## Database Schema (11 models)

| Model | Table | Key Relationships |
|-------|-------|-------------------|
| `Organization` | organizations | Multi-tenant: name, email_config (JSON), webhook_url, settings |
| `Invoice` | invoices | Central entity — status (pending→processing→done/failed/needs_review), source, approval_status, po_match_status, payment_status, vendor_id, xero_invoice_id, due_date |
| `ExtractedData` | extracted_data | 1:1 with Invoice — ~40 extraction fields (vendor/customer, totals, dates, bank) |
| `LineItem` | line_items | 1:N with Invoice — description, quantity, unit_price, tax, net/gross |
| `ExtractionConfidence` | extraction_confidence | 1:N per-field confidence + human review corrections |
| `ProcessingLog` | processing_logs | Audit trail: step, status, message, duration_ms |
| `ApiKey` | api_keys | key_hash, rate_limit, last_used_at, is_active |
| `ApprovalToken` | approval_tokens | token, email, redeemed_at for approval workflow |
| `PurchaseOrder` | purchase_orders | PO master: po_number, vendor_name, line_items (JSON), status |
| `POMatch` | po_matches | Match results + discrepancies per invoice |
| `Vendor` | vendors | Vendor master list with aliases (JSON) |
| `WebhookDelivery` | webhook_deliveries | Delivery attempts: status, response_code, attempt_number |
| `XeroCredential` | xero_credentials | encrypted tokens, tenant_id, organization_id |

**Key rule:** All FK columns use `ForeignKey("tablename.id")` and queries use `selectinload()` for relationship eager loading to avoid async lazy-load `MissingGreenlet` errors.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | /api/v1/invoices/upload | Upload single invoice |
| POST | /api/v1/invoices/upload/batch | Upload multiple invoices |
| GET | /api/v1/invoices | List with filters, sort, pagination |
| GET | /api/v1/invoices/stats | Dashboard stats + spend analytics (monthly trends, top vendors, currencies) |
| GET | /api/v1/invoices/{id} | Full detail + extracted data |
| PATCH | /api/v1/invoices/{id}/review | Human corrections |
| POST | /api/v1/invoices/{id}/reprocess | Re-run pipeline |
| DELETE | /api/v1/invoices/{id} | Remove invoice + file |
| GET | /api/v1/invoices/{id}/log | Processing audit log |
| GET | /api/v1/exports/csv | CSV export |
| GET | /api/v1/exports/json | JSON export |
| GET | /api/v1/settings/webhook | Get webhook config |
| POST | /api/v1/settings/webhook | Configure webhook |
| GET | /api/v1/settings/webhook/deliveries | Webhook delivery history |
| POST | /api/v1/admin/keys | Create API key (admin) |
| GET | /api/v1/admin/keys | List API keys (admin) |
| PATCH | /api/v1/admin/keys/{id}/deactivate | Deactivate API key (admin) |
| GET | /api/v1/approvals/{token}/approve | Approve invoice via email link |
| GET | /api/v1/approvals/{token}/reject | Reject invoice via email link |
| POST | /api/v1/purchase-orders | Create purchase order |
| GET | /api/v1/purchase-orders | List purchase orders |
| GET | /api/v1/purchase-orders/{id} | Get purchase order |
| POST | /api/v1/vendors | Create vendor master record |
| GET | /api/v1/vendors | List vendors |
| GET | /api/v1/vendors/{id} | Get vendor |
| PATCH | /api/v1/vendors/{id} | Update vendor |
| GET | /api/v1/integrations/xero/connect | Start Xero OAuth PKCE flow |
| POST | /api/v1/integrations/xero/callback | Submit Xero auth code + PKCE verifier |
| GET | /api/v1/integrations/xero/status | Check Xero connection status |
| POST | /api/v1/n8n/trigger | Trigger n8n workflow externally |
| GET | /health | Health check |

## Processing Pipeline (in `invoice_worker.py`, async per step)

1. **Ingestion** — Validate type/size, store file, create DB record
2. **Preprocessing** — PDF→PNG images, OCR fallback
3. **Extraction** — Claude Vision with structured output (`extractor.py:EXTRACTION_SCHEMA`)
4. **Validation** — Math cross-checks (subtotal+tax-discount ≈ grand_total), confidence scoring, duplicate detection
5. **Save** — Write extracted_data, line_items, confidence_scores to DB
6. **Vendor Matching** — Fuzzy-match vendor against master list (rapidfuzz, ≥80%)
7. **PO / 3-Way Matching** — Match invoice vs purchase orders; flag line-item discrepancies
8. **Payment Terms Parsing** — Parse "Net 30", "2/10 Net 30"; set due_date and payment_status
9. **Approval Workflow** — Token-based email with approve/reject links (threshold in `APPROVAL_THRESHOLD`)
10. **Webhook** — Deliver via Celery task with retry (5 attempts, exponential backoff, dead-letter + admin email alert)
11. **Airtable Sync** — Push to Airtable tables
12. **n8n Integration** — Trigger external n8n workflow (Slack + Airtable + email)
13. **Xero Sync** — Push to Xero (fully processed invoices only)

**Confidence threshold:** 0.85 (`CONFIDENCE_THRESHOLD`). Below = `needs_review` status.

## Key Conventions

- **Async all the way:** async SQLAlchemy, async HTTP for LLM, async endpoints. Celery tasks wrap async with `asyncio.run()`.
- **Eager load relationships** with `selectinload()` — never lazy-load in async context.
- **Celery tasks** imported and called as `.delay()` from routers. Tasks use `bind=True` and `self.retry()` for retry logic.
- **Models** use `Uuid` + `JSON` (not PostgreSQL-specific types) for DB-agnostic compat. All use `Mapped` + `mapped_column` style — no mixins.
- **Storage** uses `app.core.storage.storage` singleton — resolves to `LocalStorage` or `S3Storage` via `settings.storage_backend`.
- **LLM client** is a singleton at `app.core.llm_client.llm_client`. Supports Anthropic, OpenAI, and custom endpoints.
- **Auth** is API-key based: `X-API-Key` header validated against SHA-256 hash in DB, with Redis per-minute rate limiting. Bypassed in tests via `app.dependency_overrides[get_api_key]`.
- **Env vars** via pydantic-settings in `app.config.settings` — all defaults in code, override via `.env`.
- **Alembic** for migrations (`alembic upgrade head`). `init_db()` in database.py creates tables for dev only.
- **Pipeline steps after Save are non-fatal** — vendor matching, PO matching, payment terms, approval, webhook, Airtable, n8n, Xero all catch exceptions and log warnings instead of failing the invoice.

## LLM Extraction Schema

Defined in `extractor.py:EXTRACTION_SCHEMA`. The extractor sends invoice images to Claude with a system prompt defining rules (exact values, null on missing, ISO currency, YYYY-MM-DD dates). Returns ~30 header fields + line items array + per-field confidence scores.

The LLM client (`core/llm_client.py`) handles multi-provider dispatch (Anthropic vs OpenAI vs custom), base64 image encoding, SSE streaming (for OpenAI), markdown code fence stripping, and automatic retries (3 attempts with exponential backoff).

## Celery Tasks (invoice_worker.py)

| Task | Schedule | Queue | Purpose |
|------|----------|-------|---------|
| `process_invoice` | On-demand | main | Full pipeline |
| `deliver_webhook` | On-demand (retry) | main | Webhook with 5 retries, exponential backoff, dead-letter |
| `check_email` | Every 300s | monitoring | IMAP inbox poll |
| `cleanup_temp` | Every 3600s | maintenance | Temp file cleanup |
| `check_payment_due_dates` | Every 86400s | maintenance | Mark overdue + send reminders |

Celery config: `task_acks_late=True`, `worker_prefetch_multiplier=1`, soft/hard time limits (600s/900s).

## Auth & Security

- **API Key auth** (`X-API-Key` header) required on all routes except `/health`, `/`, admin routes (use `X-Admin-Key`), and approval links (token-based, no auth).
- **Admin routes** use a separate `X-Admin-Key` header checked against `settings.admin_api_key`.
- **API keys** are created via admin endpoints with `ip_` prefix + `secrets.token_urlsafe(32)`. Stored as SHA-256 hash; raw key shown once.
- **Rate limiting** per API key via Redis sorted by minute bucket.
- **Xero OAuth** uses PKCE flow (no client_secret needed) — designed for Desktop app flow where user manually copies auth code.

## Configuration (.env)

See `.env.example` for all vars. Key groups:
- **Core:** `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `ADMIN_API_KEY`
- **LLM:** `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (default: `claude-sonnet-5-20250601`), `LLM_PROVIDER`
- **Processing:** `CONFIDENCE_THRESHOLD` (0.85), `MAX_FILE_SIZE_MB`, `ALLOWED_EXTENSIONS`
- **Storage:** `STORAGE_BACKEND` (local|s3), `STORAGE_PATH`
- **Email Monitoring:** `EMAIL_HOST`, `EMAIL_USERNAME`, `EMAIL_PASSWORD`
- **SMTP:** `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` (for approval emails + dead-letter alerts)
- **Approval Workflow:** `APPROVAL_THRESHOLD`, `APPROVAL_BASE_URL`, `APPROVAL_RECIPIENT_EMAIL`
- **Airtable:** `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID`, `AIRTABLE_SYNC_ENABLED`
- **Xero:** `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET`, `XERO_ENABLED`, `XERO_REDIRECT_URI`
- **n8n:** `N8N_WEBHOOK_URL`, `N8N_ENABLED`

## Development Commands

```bash
# Install
pip install -e ".[dev]"

# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=term

# Run a specific test file
pytest tests/test_api.py -v

# Run a single test
pytest tests/test_api.py::test_upload_invoice -v

# Lint
ruff check .

# Format check
ruff format --check .

# Auto-format
ruff format .

# Type check
mypy app

# Run the API server
uvicorn app.main:app --reload

# Run the Celery worker
celery -A app.workers.invoice_worker.celery_app worker --loglevel=info

# Run database migrations
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "description"

# Start dependencies only
docker compose up postgres redis -d

# Full stack
docker compose up -d
```

## Testing Patterns

- Tests use SQLite via aiosqlite (set via `os.environ["DATABASE_URL"]` in `conftest.py`).
- Celery `.delay()` is mocked via `patch("app.routers.invoices.process_invoice_task.delay")` in the `client` fixture.
- API key auth is bypassed in tests via `app.dependency_overrides[get_api_key]`.
- Database is recreated per test (`autouse=True` fixture: `create_all` before, `drop_all` after).
- 12 test files in `tests/`: `test_api.py`, `test_ingestion.py`, `test_validator.py`, `test_preprocessor.py`, `test_storage.py`, `test_auth.py`, `test_approval_flow.py`, `test_po_matching.py`, `test_xero_sync.py`, `test_integrations.py`, and more.

## CI/CD

GitHub Actions workflow (`.github/workflows/ci.yml`) on push/PR to master:
1. **Lint job:** `ruff check .` + `ruff format --check .`
2. **Test job:** Runs against real Postgres + Redis services, executes `pytest --cov=app --cov-report=xml --cov-fail-under=60`
3. **Docker Build job:** Builds image (no push)
