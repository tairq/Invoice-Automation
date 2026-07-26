# Changelog

## Tier 4 — Spend Analytics + README Upgrade

### Added
- Spend analytics on `GET /api/v1/invoices/stats`: `total_amount_by_currency`, `avg_processing_time_seconds`, `top_vendors` (top 10 by spend, last 90 days), `monthly_spend` (last 12 months), `anomaly_rate` (optional)
- Architecture diagram (Mermaid) in README
- "Why this project" section in README
- Railway.app deployment instructions in README
- Fixed roadmap checkboxes in README

## Tier 3 — n8n Integration Showcase

### Added
- `POST /api/v1/n8n/trigger` endpoint for external n8n workflow triggering
- Automatic n8n webhook call at end of Celery pipeline (when `N8N_ENABLED=true`)
- `N8N_WEBHOOK_URL` and `N8N_ENABLED` configuration variables
- `docs/n8n-starter-workflow.json` — importable n8n workflow with Webhook → Slack → Airtable
- README "n8n Integration" section with flow diagram

## Tier 2 — Webhook Reliability (Retry + Dead Letter)

### Added
- `WebhookDelivery` model tracking each delivery attempt (status, response code, error)
- `deliver_webhook` Celery task with `autoretry_for`, `max_retries=5`, exponential backoff (max 5 min)
- Dead-letter logic: after 5 failed attempts, status→`dead_letter` + admin alert email
- `GET /api/v1/settings/webhook/deliveries` endpoint with invoice_id / status filtering
- `ADMIN_EMAIL` configuration variable
- Alembic migration `004_add_webhook_delivery.py`

### Changed
- Webhook firing moved from inline `_fire_webhook()` to async Celery task `deliver_webhook.delay()`

## Tier 1 — GitHub Actions CI/CD Pipeline

### Added
- `.github/workflows/ci.yml` with lint, test, and docker-build jobs
- CI badge in README
- Test job spins up PostgreSQL 15 and Redis 7 as service containers
- Coverage threshold set at 60% (`--cov-fail-under=60`)
- Pip caching via `actions/cache` keyed on `pyproject.toml` hash
