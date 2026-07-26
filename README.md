# 📄 Invoice Processing Automation System

AI-powered invoice processing that handles **any kind of invoice** automatically — PDFs, scanned images, digital invoices — extracting structured data with Claude AI and processing them through a complete pipeline from ingestion to export.

## ✨ Features

- **🤖 AI-Powered Extraction** — Uses Claude Vision API to extract invoice data from any layout without training
- **📎 Multi-Format Support** — PDF, JPG, PNG, TIFF, email attachments (.eml)
- **📤 Multiple Ingestion Sources** — Manual upload via API, email monitoring (IMAP), folder watch
- **📊 Structured Data Extraction** — Vendor info, line items, totals, taxes, bank details, and more
- **✅ Smart Validation** — Cross-field math checks, confidence scoring, duplicate detection
- **⚠️ Human Review Queue** — Low-confidence invoices flagged for manual correction
- **📬 Webhook Notifications** — Real-time alerts on processing completion
- **📥 CSV/JSON Export** — Download processed data for accounting integration
- **📊 Airtable Sync** — Push invoice data directly to Airtable tables
- **🐳 Docker Ready** — Full containerized deployment with Docker Compose

## 🏗️ Architecture

```
                    ┌──────────────────┐
                    │   INGESTION       │
                    │  Upload │  Email  │
                    └────┬─────┘
                         ▼
                    ┌──────────────────┐
                    │  PREPROCESSING   │
                    │  PDF→Images, OCR │
                    └────┬─────────────┘
                         ▼
                    ┌──────────────────┐
                    │  AI EXTRACTION   │
                    │  (Claude Vision) │
                    └────┬─────────────┘
                         ▼
                    ┌──────────────────┐
                    │   VALIDATION     │
                    │  Math + Conf +   │
                    │  Duplicate Check │
                    └────┬──────┬──────┘
                         │      │
                    ┌────▼──┐ ┌▼────────┐
                    │  Done │ │  Review │
                    └───┬───┘ └───┬─────┘
                        │         │
                    ┌───▼─────────▼──────┐
                    │   POST-PROCESSING   │
                    │  Save · Export ·    │
                    │  Webhook · Airtable │
                    └────────────────────┘
```

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
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

## 📚 API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/invoices/upload` | Upload a single invoice |
| `POST` | `/api/v1/invoices/upload/batch` | Upload multiple invoices |
| `GET` | `/api/v1/invoices` | List invoices (filter, sort, paginate) |
| `GET` | `/api/v1/invoices/{id}` | Get invoice detail + extracted data |
| `PATCH` | `/api/v1/invoices/{id}/review` | Human review — correct fields |
| `POST` | `/api/v1/invoices/{id}/reprocess` | Re-run extraction |
| `DELETE` | `/api/v1/invoices/{id}` | Delete invoice |
| `GET` | `/api/v1/invoices/stats` | Dashboard statistics |
| `GET` | `/api/v1/invoices/{id}/log` | Processing audit log |
| `GET` | `/api/v1/exports/csv` | Export invoices as CSV |
| `GET` | `/api/v1/exports/json` | Export invoices as JSON |
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

## 🔧 Configuration

Key environment variables (see `.env.example`):

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql+asyncpg://...` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `ANTHROPIC_API_KEY` | Claude API key | — |
| `ANTHROPIC_MODEL` | Claude model | `claude-sonnet-5-20250601` |
| `CONFIDENCE_THRESHOLD` | Auto-pass threshold | `0.85` |
| `STORAGE_BACKEND` | `local` or `s3` | `local` |
| `EMAIL_HOST` | IMAP server for email monitoring | — |

## 🧪 Testing

```bash
# Run all tests
pytest

# With coverage
pytest --cov=app --cov-report=term

# Run specific test file
pytest tests/test_api.py -v
```

## 📁 Project Structure

```
invoice-processor/
├── app/
│   ├── main.py                 # FastAPI app entry point
│   ├── config.py               # Settings (pydantic-settings)
│   ├── database.py             # SQLAlchemy engine & session
│   ├── models/                 # Database models (SQLAlchemy)
│   │   ├── invoice.py          # Central invoice entity
│   │   ├── extracted_data.py   # Structured extraction output
│   │   ├── line_item.py        # Invoice line items
│   │   ├── extraction_confidence.py
│   │   ├── organization.py     # Multi-tenant support
│   │   └── processing_log.py   # Audit trail
│   ├── schemas/                # Pydantic request/response schemas
│   ├── routers/                # API route handlers
│   │   ├── invoices.py         # CRUD + upload + review endpoints
│   │   ├── exports.py          # CSV/JSON export
│   │   └── webhooks.py         # Webhook configuration
│   ├── services/               # Business logic
│   │   ├── ingestion.py        # File validation, storage, dedup
│   │   ├── preprocessor.py     # PDF→images, OCR fallback
│   │   ├── extractor.py        # AI-powered extraction
│   │   ├── validator.py        # Cross-field validation
│   │   ├── exporter.py         # CSV/JSON generation
│   │   └── email_monitor.py    # IMAP inbox watching
│   ├── core/                   # Core utilities
│   │   ├── storage.py          # Local/S3 file storage
│   │   ├── ocr.py              # Tesseract OCR wrapper
│   │   └── llm_client.py       # Claude API client
│   ├── workers/
│   │   └── invoice_worker.py   # Celery async pipeline
│   └── migrations/             # Alembic database migrations
├── tests/                      # Pytest test suite
├── docker-compose.yml          # Full stack orchestration
├── Dockerfile                  # Container build
└── pyproject.toml              # Project config & dependencies
```

## 🔄 Processing Pipeline

1. **Ingestion** — File validated, deduplicated, stored, and queued
2. **Preprocessing** — Converted to images (PDF→PNG, etc.)
3. **AI Extraction** — Claude Vision analyzes and extracts all fields
4. **Validation** — Math cross-checks, confidence scoring, duplicate detection
5. **Storage** — Results saved to PostgreSQL database
6. **Notification** — Webhook fired on completion
7. **Review** (if needed) — Low-confidence invoices queued for manual correction

## 🛣️ Roadmap

- [x] Core extraction pipeline (upload → AI → validation → storage)
- [x] Multi-format support (PDF, images, TIFF)
- [x] REST API with full CRUD
- [x] Async processing with Celery
- [x] Airtable sync (replaces Streamlit dashboard)
- [x] Email monitoring (IMAP)
- [x] CSV/JSON export
- [x] Docker deployment
- [ ] Authentication & API keys
- [ ] S3 storage backend
- [ ] Accounting software integration (QuickBooks, Xero)
- [ ] Multi-language invoice support
- [ ] Batch processing optimizations
- [ ] Performance benchmark suite

## 📄 License

MIT
