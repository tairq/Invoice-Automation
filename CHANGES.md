# Changelog

## 2026-07-26 — API Key Auth, S3 Presigned URLs, Xero Integration

### Feature 1: API Key Authentication
- **New model**: `app/models/api_key.py` — ApiKey (id, key_hash, client_name, is_active, rate_limit_per_minute, created_at, last_used_at)
- **New dependency**: `app/core/auth.py` — `get_api_key()` reads X-API-Key header, SHA-256 hashes, queries DB, enforces rate limits via Redis
- **New module**: `app/core/redis.py` — Redis connection pool singleton for rate limiting
- **New routes**: `app/routers/admin.py` — POST/GET/PATCH `/api/v1/admin/keys` (protected by X-Admin-Key header)
- **Auth applied** to all existing routers (invoices, exports, webhooks) via router-level `dependencies=[Depends(get_api_key)]`
- Health endpoint (`/health`, `/`) remains unauthenticated
- Rate limiting degrades gracefully if Redis is unavailable (logs warning, allows request)
- Alembic migration `001_add_api_keys_table.py`

### Feature 2: S3 Presigned URLs
- Added `get_url(file_path, expires_in=3600)` to `BaseStorage`, `LocalStorage`, `S3Storage` in `app/core/storage.py`
- S3 returns presigned URLs via `generate_presigned_url`; LocalStorage returns `file://` URIs
- All existing S3 infrastructure (S3Storage class, boto3 dependency, config fields) was already present

### Feature 3: Xero Integration
- **New model**: `app/models/xero_credential.py` — XeroCredential (org_id, access_token, refresh_token, token_expires_at, tenant_id)
- **New service**: `app/services/xero_sync.py` — OAuth2 flow helpers, data mapping, invoice push with auto-refresh
- **New routes**: `app/routers/integrations.py` — GET `/xero/connect`, `/xero/callback`, `/xero/status`
- **Pipeline integration**: Xero sync (Step 8) in `invoice_worker.py` — runs after Airtable sync for `done` status invoices
- Added `xero_invoice_id` column to `app/models/invoice.py`
- Token auto-refresh (5-min buffer); 401 retry once with refreshed token
- If `XERO_ENABLED=false`, all Xero operations skip silently
- Alembic migration `002_add_xero_tables.py`

### Config Changes
- `app/config.py`: Added `admin_api_key`, `xero_client_id`, `xero_client_secret`, `xero_enabled`, `xero_redirect_uri`
- `.env.example`: Documented new vars (ADMIN_API_KEY, XERO_*) with defaults/comments
- `app/main.py`: Admin and integrations routers registered; Redis cleanup in shutdown

### Testing
- `tests/test_auth.py` — 6 tests: missing/invalid/valid/deactivated key, health bypass, export auth
- `tests/test_storage.py` — 3 tests: URL generation, nonexistent file, custom expiry
- `tests/test_xero_sync.py` — 3 tests: basic mapping, missing data error, empty line items
- `tests/test_integrations.py` — 4 tests: connect URL, status check, callback validation, success path
- `tests/conftest.py` — Auth bypass for existing tests; dependency override cleanup
- **All 37 tests pass**
