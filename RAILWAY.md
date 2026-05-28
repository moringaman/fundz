# Railway Deployment Guide

## Architecture

```
Railway Project: fundz
├── Postgres (managed plugin)
├── Backend  (Docker, nixpacks from backend/)
└── Frontend (Docker, nixpacks from frontend/)
```

Backend and frontend communicate via Railway's internal DNS (`backend.railway.internal`).

## Deployment Steps

### 1. Create Railway Project

`railway init` or via dashboard → New Project → Deploy from GitHub repo.

### 2. Add Postgres

Railway dashboard → New → Database → Postgres. This auto-sets `DATABASE_URL`, `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`.

### 3. Deploy Backend

Set root directory to `backend/`. Railway auto-detects the Python Dockerfile.

#### Required Environment Variables

| Variable | Source | Notes |
|----------|--------|-------|
| `DATABASE_URL` | Railway Postgres | Auto-set by plugin |
| `CLERK_SECRET_KEY` | Clerk Dashboard → API Keys | Secret key |
| `CLERK_PUBLISHABLE_KEY` | Clerk Dashboard → API Keys | Public key |
| `CLERK_JWKS_URL` | Clerk | `https://<domain>.clerk.accounts.dev/.well-known/jwks.json` |
| `CREDENTIAL_ENCRYPTION_KEY` | Self-generated | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `REDIS_URL` | Railway Redis (optional) | For caching |
| `OPENROUTER_API_KEY` | OpenRouter | Fallback if not in credential store |
| `LLM_PROVIDER` | You | `openrouter` (default) |
| `LLM_MODEL` | You | `openai/gpt-4o-mini` (default) |

#### Optional Variables

| Variable | Notes |
|----------|-------|
| `MAIL_SERVER_API_KEY` | Email reports (Resend/Mailgun) |
| `MAIL_TO_ADDRESS` | Report recipient |
| `MAIL_FROM_ADDRESS` | Sender |
| `PHEMEX_API_KEY` / `PHEMEX_API_SECRET` | Fallback if not in credential store |
| `HYPERLIQUID_WALLET_ADDRESS` / `HYPERLIQUID_WALLET_KEY` | Fallback |

### 4. Deploy Frontend

Set root directory to `frontend/`. Railway auto-detects the Node+Dockerfile.

#### Required Environment Variables

| Variable | Value |
|----------|-------|
| `BACKEND_HOST` | `backend.railway.internal` |
| `VITE_CLERK_PUBLISHABLE_KEY` | Same as backend's CLERK_PUBLISHABLE_KEY |
| `VITE_API_URL` | `/api` (proxied via nginx) |

### 5. Configure Clerk

Add Railway domain to Clerk Dashboard → Redirect URLs:
- `https://<railway-domain>.up.railway.app`

## Local Development

```bash
# Start all services
docker compose up -d

# Rebuild after code changes
docker compose up -d --build

# View logs
docker compose logs -f backend
```

## Health Check

`GET /api/health` returns `{"status": "ok", "version": "1.0.0"}`. Railway uses this endpoint.

## Credential Store

Exchange and LLM API keys are stored encrypted in the `tenant_credentials` table using Fernet symmetric encryption. The `.env` keys serve as fallback. To migrate:

```python
# Keys auto-migrate when saved through Settings UI
# or manually:
from app.services.credential_service import save_credential
await save_credential(tenant_id, "openrouter", "api_key", "sk-or-...")
```
