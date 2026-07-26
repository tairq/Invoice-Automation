# Invoice Processing Automation

**Stack:** Python 3.12 | FastAPI | SQLAlchemy 2.0 (async) | Celery + Redis | PostgreSQL | Claude API | Airtable

## Project Structure

```
app/
├── main.py            # FastAPI app, lifespan, exception handlers, health check
├── config.py          # pydantic-settings: .env → Settings singleton
├── database.py        # async engine, session factory, Base, get_session dependency
├── models/            # 6 SQLAlchemy models (see schema below)
├── schemas/           # Pydantic request/response models (invoice.py + __init__.py)
├── routers/           # FastAPI routers: invoices.py, exports.py, webhooks.py
├── services/          # Business logic: ingestion, preprocessor, extractor, validator, exporter, airtable_sync, email_monitor
├── core/              # Infrastructure: storage (local/S3), ocr (Tesseract), llm_client (Claude)
├── workers/           # Celery tasks: invoice_worker.py (pipeline + beat schedule)
└── migrations/        # Alembic (env.py, script.py.mako)
```

## Database Schema (6 tables)

- **invoices** — Central entity: status (pending→processing→done/failed/needs_review), source (upload/email/folder/api), file_path, confidence_score, needs_review, is_duplicate
- **extracted_data** — 1:1 with invoices: vendor/customer info, line totals, dates, bank details, ~40 extraction fields
- **line_items** — 1:N with invoices: description, quantity, unit_price, tax rates, net/gross amounts
- **extraction_confidence** — 1:N: per-field confidence tracking from LLM/OCR/regex, with human review corrections
- **organizations** — Multi-tenant: name, email_config (JSON), webhook_url, settings (JSON)
- **processing_logs** — Audit trail: step, status, message, duration_ms

**Key rule:** All FK columns use `ForeignKey("tablename.id")` and queries use `selectinload()` for relationship eager loading to avoid async lazy-load `MissingGreenlet` errors.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | /api/v1/invoices/upload | Upload single invoice |
| POST | /api/v1/invoices/upload/batch | Upload multiple invoices |
| GET | /api/v1/invoices | List with filters, sort, pagination |
| GET | /api/v1/invoices/stats | Dashboard aggregation |
| GET | /api/v1/invoices/{id} | Full detail + extracted data |
| PATCH | /api/v1/invoices/{id}/review | Human corrections |
| POST | /api/v1/invoices/{id}/reprocess | Re-run pipeline |
| DELETE | /api/v1/invoices/{id} | Remove invoice + file |
| GET | /api/v1/invoices/{id}/log | Processing audit log |
| GET | /api/v1/exports/csv | CSV export |
| GET | /api/v1/exports/json | JSON export |
| POST | /api/v1/settings/webhook | Configure webhook |
| GET | /api/v1/settings/webhook | Get webhook config |
| GET | /health | Health check |

## Processing Pipeline (in invoice_worker.py)

1. **Ingestion** (ingestion.py) — Validate type/size, store file, create DB record
2. **Preprocessing** (preprocessor.py) — PDF→PNG images, OCR fallback
3. **Extraction** (extractor.py → core/llm_client.py) — Claude Vision with structured output
4. **Validation** (validator.py) — Math cross-checks, confidence scoring, duplicate detection
5. **Save** — Write extracted_data, line_items, confidence_scores to DB
6. **Webhook** — Fire notification on completion
7. **Airtable** — Push invoice data to Airtable tables

**Confidence threshold:** 0.85 (configurable). Below = `needs_review` status.

## LLM Extraction Schema

The extractor sends invoice images to Claude with a system prompt defining rules (exact values, null on missing, ISO currency, YYYY-MM-DD dates). Expected return shape defined in `extractor.py:EXTRACTION_SCHEMA`.

## Celery Tasks (invoice_worker.py)

- `process_invoice` — Full pipeline (main queue)
- `check_email` — IMAP poll (beat schedule: every 300s)
- `cleanup_temp` — Temp file cleanup (beat schedule: every 3600s)

## Configuration (.env)

Key vars: `DATABASE_URL`, `REDIS_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `CONFIDENCE_THRESHOLD`, `STORAGE_BACKEND`, `EMAIL_HOST/USERNAME/PASSWORD`, `AIRTABLE_API_KEY`, `AIRTABLE_BASE_ID`. See `.env.example`.

## Development

```bash
pip install -e ".[dev]"
pytest                    # 21 tests
uvicorn app.main:app --reload
celery -A app.workers.invoice_worker worker --loglevel=info
docker compose up -d      # Full stack
```

## Key Conventions

- **Async all the way:** async SQLAlchemy, async HTTP for LLM, async endpoints
- **Eager load relationships** with `selectinload()` — never lazy-load in async
- **Celery tasks** imported and called as `.delay()` from routers
- **Models** use `Uuid` + `JSON` (not PostgreSQL-specific types) for DB-agnostic compat
- **Storage** uses `app.core.storage.storage` singleton — LocalStorage or S3Storage
- **LLM client** is a singleton at `app.core.llm_client.llm_client`
- **Env vars** via pydantic-settings in `app.config.settings`
- **Tests** mock Celery `.delay()` and use SQLite via aiosqlite

## Models Location

All models at `app/models/` — see `__init__.py` for exports. Mixin-free, use `Mapped` + `mapped_column` style throughout.
