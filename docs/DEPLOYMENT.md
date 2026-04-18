# Phemex AI Trader — Deployment Guide

Production deployment guide for the AI-powered trading platform.

---

## Architecture Overview

| Service         | Tech              | Port | Notes                          |
| --------------- | ----------------- | ---- | ------------------------------ |
| **Frontend**    | React + Nginx     | 3000 | SPA with API reverse-proxy     |
| **Backend**     | FastAPI + Uvicorn | 8000 | REST + WebSocket, async Python |
| **Database**    | PostgreSQL 15     | 5432 | Persistent storage             |
| **Cache/PubSub**| Redis 7           | 6379 | WebSocket broadcast, caching   |

**Key requirements:** WebSocket support (real-time market data & team chat), background tasks (agent scheduler, position monitoring), persistent storage.

---

## How Database Migrations Work

> **Important:** This app does **not** use Alembic for production deployments.

On every startup the FastAPI lifespan handler runs `Base.metadata.create_all`, which creates every table from the SQLAlchemy models. It then applies incremental `ALTER TABLE` migrations for columns added after the initial schema. This means:

- **First deploy:** all tables are created automatically when the backend first starts.
- **Subsequent deploys:** any new columns are added automatically — no manual migration step required.
- Alembic migrations in `alembic/versions/` exist for local development history only. **Do not run `alembic upgrade head` in production** — it would only apply 2 partial migrations and leave the schema incomplete.

---

## Pre-Deployment Checklist

Before deploying to any platform:

### 1. Create production `.env` files

**`backend/.env.production`**
```env
APP_NAME=phemex-ai-trader
APP_VERSION=1.0.0
DEBUG=false
HOST=0.0.0.0
PORT=8000

# Database — set by hosting provider
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/phemex_ai_trader

# Redis — set by hosting provider
REDIS_URL=redis://host:6379/0

# Phemex API
PHEMEX_API_KEY=your_production_api_key
PHEMEX_API_SECRET=your_production_api_secret
PHEMEX_TESTNET=false

# LLM
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_openrouter_key
LLM_MODEL=mistralai/mixtral-8x7b-instruct
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=1000

# Auth — generate with: python3 -c "import secrets; print(secrets.token_urlsafe(64))"
JWT_SECRET=CHANGE_ME_TO_A_RANDOM_64_CHAR_STRING
JWT_ALGORITHM=HS256
JWT_EXPIRATION_MINUTES=10080

# CORS — your production frontend URL
CORS_ORIGINS=https://your-app.example.com

# Email (optional)
MAIL_SERVER_DOMAIN=your-email-service.com
MAIL_SERVER_API_KEY=your_mail_key
MAIL_TO_ADDRESS=trading@yourdomain.com
MAIL_FROM_ADDRESS=noreply@yourdomain.com
MAIL_DAILY_HOUR=17

# Rate limiting
RATE_LIMIT_PER_MINUTE=120
```

**`frontend/.env.production`**
```env
VITE_API_URL=/api
```

### 2. Security hardening

```bash
# Generate a strong JWT secret
python3 -c "import secrets; print(secrets.token_urlsafe(64))"

# Never commit .env files — ensure they're in .gitignore
echo "*.env.production" >> .gitignore
```

### 3. Verify Docker builds work locally

```bash
docker compose build --no-cache
docker compose up -d
# Test: http://localhost:3001 (frontend), http://localhost:8000/docs (API docs)
docker compose down
```

---

## Option A: Railway (Recommended)

**Cost:** ~$5/mo hobby plan (includes $5 credit) — typically $8-15/mo total for this stack.

**Why Railway:**
- Native Docker support with auto-deploy from GitHub
- Managed PostgreSQL & Redis add-ons (included in plan)
- WebSocket support out of the box
- Zero-config SSL/HTTPS
- Simple environment variable management
- One-click rollbacks

### Step 1 — Create Railway project

1. Go to [railway.app](https://railway.app) and sign in with GitHub
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select the `phemex-ai-trader` repository

### Step 2 — Add PostgreSQL & Redis

1. In your project dashboard, click **"+ New"** → **"Database"** → **"PostgreSQL"**
2. Click **"+ New"** → **"Database"** → **"Redis"**
3. Railway auto-provisions both and sets connection URLs

### Step 3 — Configure Backend service

1. Click on the auto-created service (or **"+ New"** → **"GitHub Repo"**, root dir `backend`)
2. **Settings** tab:
   - **Root Directory:** `backend`
   - **Builder:** Dockerfile
   - No start command override needed — the Dockerfile CMD handles `$PORT` automatically
3. **Variables** tab — add all backend env vars:

```
# Database — use the individual PG* reference variables (Railway injects these automatically
# from the Postgres add-on). The app builds postgresql+asyncpg:// from them at startup.
# Do NOT use DATABASE_URL — Railway sets it to postgresql:// which breaks asyncpg.
PGHOST     = ${{Postgres.PGHOST}}
PGPORT     = ${{Postgres.PGPORT}}
PGUSER     = ${{Postgres.PGUSER}}
PGPASSWORD = ${{Postgres.PGPASSWORD}}
PGDATABASE = ${{Postgres.PGDATABASE}}

# Redis
REDIS_URL           = ${{Redis.REDIS_URL}}

# Core
DEBUG               = false
JWT_SECRET          = <generated 64-char secret>   # python3 -c "import secrets; print(secrets.token_urlsafe(64))"
JWT_ALGORITHM       = HS256
JWT_EXPIRATION_MINUTES = 10080

# CORS — set to your frontend domain after Step 5
CORS_ORIGINS        = https://your-frontend.up.railway.app

# Phemex API
PHEMEX_API_KEY      = <your_production_api_key>
PHEMEX_API_SECRET   = <your_production_api_secret>
PHEMEX_TESTNET      = false

# LLM (at least one required for agents to function)
LLM_PROVIDER        = openrouter
OPENROUTER_API_KEY  = <your_openrouter_key>
LLM_MODEL           = openai/gpt-4o-mini
LLM_TEMPERATURE     = 0.7
LLM_MAX_TOKENS      = 1000

# Email (optional)
MAIL_SERVER_DOMAIN  = <your_email_service>
MAIL_SERVER_API_KEY = <your_mail_key>
MAIL_TO_ADDRESS     = trading@yourdomain.com
MAIL_FROM_ADDRESS   = noreply@yourdomain.com
MAIL_DAILY_HOUR     = 17
```

> **DATABASE_URL note:** Railway's `${{Postgres.DATABASE_URL}}` uses `postgresql://` (no asyncpg driver) and can inject an empty `PGPORT` during container startup, causing a crash. The app's `config.py` builds the correct `postgresql+asyncpg://` URL from the individual `PGHOST`/`PGPORT`/`PGUSER`/`PGPASSWORD`/`PGDATABASE` variables, which Railway auto-links from the Postgres add-on. Use those instead of `DATABASE_URL`.

### Step 4 — Configure Frontend service

1. Click **"+ New"** → **"GitHub Repo"** (same repo, different service)
2. **Settings** tab:
   - **Root Directory:** `frontend`
   - **Builder:** Dockerfile
3. **Variables** tab:
   ```
   BACKEND_HOST = backend.railway.internal
   ```

> **How it works:** The `frontend/nginx.conf` uses `${BACKEND_HOST}` as a placeholder. The Dockerfile runs `envsubst` at container startup to substitute the backend hostname into the nginx config. On Railway, this points to the backend via private networking. For local docker-compose it defaults to `phemex-ai-trader-backend` (the Docker container name) — no change needed locally.

### Step 5 — Configure networking

1. **Backend** → Settings → Networking → Generate a **public domain** (for direct API access) or use Railway's private networking
2. **Frontend** → Settings → Networking → Generate a **public domain** (this is your app URL)
3. Frontend talks to backend via Railway's private network (`*.railway.internal`)

### Step 6 — Deploy

Railway auto-deploys on every `git push` to `main`. First deploy:
```bash
git add -A && git commit -m "deploy: production configuration"
git push origin main
```

Watch the backend deployment logs. The lifespan handler will:
1. Run `create_all` — creates all PostgreSQL tables from the ORM models
2. Apply incremental column migrations (ALTER TABLE)
3. Seed strategy overrides
4. Start the agent scheduler, market broadcast, and whale intelligence

### Step 7 — Seed the database (run once)

PostgreSQL enforces foreign-key constraints. The app uses a hardcoded `"default-user"` ID as the `user_id` FK for positions, trades, and balances — so that row must exist before any agents or trades can be created.

In the Railway dashboard → Backend service → **"Shell"** tab:

```bash
# From inside the backend container (/app)
python scripts/seed_production.py
```

This creates:
- A `default-user` account (username: `admin`, password: `ChangeMe123!`)
- A `position_sync` service account (used internally by the live position sync service)
- Initial USDT paper trading balance ($50,000)
- Three fund traders (Alex, Jordan, Sam) backed by different LLM models
- Three starter agents (Momentum Rider, Mean Reversion, Breakout Hunter)

> **Security:** Change the default `admin` password immediately after first login.

### Step 8 — Verify deployment

1. Open your frontend public domain — the dashboard should load
2. Check **Settings** → risk limits, trading gates, and 15 default pairs should all be visible
3. Check **Fund** → three traders should appear
4. Check **Agents** → three starter agents should be listed
5. Backend health: `https://your-backend.up.railway.app/health` → `{"status":"ok"}`

### Step 9 — Configure live trading via the Settings UI

All trading settings are stored in the database and configured through the UI (not environment variables):

1. **Settings → API Keys** — enter your Phemex API key and secret
2. **Settings → LLM Config** — confirm your provider key is active
3. **Settings → Trading Preferences** — review the 15 default pairs, adjust as needed
4. **Settings → Risk Limits** — review max position size, daily loss limit, leverage
5. **Settings → Trading Gates** — review confidence thresholds and session filters
6. **Agents** → enable the agents you want to run

> Telegram notifications are also configured via **Settings → Telegram** in the UI.

---

## Option B: DigitalOcean Droplet (Cheapest Self-Managed)

**Cost:** $6/mo (Basic Droplet, 1 vCPU, 1GB RAM, 25GB SSD) — runs everything via Docker Compose on a single VPS.

**Why DO Droplet:**
- Cheapest option for a multi-service app
- Full control over the server
- Docker Compose works as-is (same as local dev)
- Predictable flat monthly cost

### Step 1 — Create Droplet

1. Go to [cloud.digitalocean.com](https://cloud.digitalocean.com)
2. Create → Droplets → **Ubuntu 24.04** → **Basic** → **$6/mo** (1 vCPU, 1GB RAM)
3. Add your SSH key
4. Create Droplet — note the IP address

### Step 2 — Initial server setup

```bash
# SSH into the droplet
ssh root@YOUR_DROPLET_IP

# Update system
apt update && apt upgrade -y

# Install Docker & Docker Compose
curl -fsSL https://get.docker.com | sh
apt install -y docker-compose-plugin

# Add swap (recommended for 1GB RAM)
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# Create deploy user
adduser deploy
usermod -aG docker deploy
su - deploy
```

### Step 3 — Clone and configure

```bash
# As deploy user
cd ~
git clone https://github.com/YOUR_USER/phemex-ai-trader.git
cd phemex-ai-trader

# Create production env files
cp backend/.env.example backend/.env
nano backend/.env   # Edit with production values

cp frontend/.env frontend/.env
nano frontend/.env  # VITE_API_URL=/api
```

### Step 4 — Production Docker Compose override

Create `docker-compose.prod.yml`:

```yaml
# docker-compose.prod.yml — production overrides
services:
  backend:
    restart: always
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
    volumes: []  # Remove dev volume mount
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:${POSTGRES_PASSWORD}@db:5432/phemex_ai_trader

  frontend:
    restart: always
    ports:
      - "80:3000"
      - "443:3000"
    volumes:
      - ./frontend/nginx.conf:/etc/nginx/conf.d/default.conf:ro

  db:
    restart: always
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}

  redis:
    restart: always
```

### Step 5 — Deploy

```bash
# Set database password
export POSTGRES_PASSWORD=$(openssl rand -base64 32)
echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" >> ~/.env

# Build and start
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# Tables are created automatically on first startup via create_all.
# Seed required base data (run once after first deploy):
docker compose exec backend python scripts/seed_production.py

# Verify
docker compose ps
curl http://localhost/api/health
```

### Step 6 — SSL with Caddy (free HTTPS)

Install Caddy as a reverse proxy for automatic SSL:

```bash
# As root
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update && apt install caddy
```

Create `/etc/caddy/Caddyfile`:
```
your-domain.com {
    reverse_proxy localhost:3001
}
```

```bash
# Update docker-compose to NOT bind port 80/443 on frontend
# Instead use port 3001 (already the default)
systemctl enable caddy
systemctl start caddy
```

Caddy auto-provisions Let's Encrypt SSL certificates.

### Step 7 — Auto-deploy with GitHub Actions (optional)

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy to DigitalOcean
on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DO_HOST }}
          username: deploy
          key: ${{ secrets.DO_SSH_KEY }}
          script: |
            cd ~/phemex-ai-trader
            git pull origin main
            docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
            docker compose exec -T backend python scripts/seed_production.py
```

Add `DO_HOST` and `DO_SSH_KEY` to GitHub repo → Settings → Secrets.

---

## Option C: Render (Free Tier Available)

**Cost:** $0-14/mo — free tier for web services (with 15-min sleep), $7/mo for managed PostgreSQL, free Redis.

**Why Render:**
- Generous free tier for testing
- Managed databases
- Docker support
- WebSocket support
- Auto-deploy from GitHub

### Quick setup

1. Sign up at [render.com](https://render.com)
2. **New** → **PostgreSQL** → Free tier
3. **New** → **Redis** → Free tier
4. **New** → **Web Service** → Docker → Root: `backend` → set env vars
5. **New** → **Static Site** → Root: `frontend` → Build: `npm run build` → Publish: `dist`

> **Caveat:** Free tier services sleep after 15 minutes of inactivity. The agent scheduler and position monitoring won't work on the free tier — you need the $7/mo "Starter" plan for always-on services.

---

## Platform Comparison

| Feature               | Railway       | DO Droplet    | Render        | Heroku        |
| --------------------- | ------------- | ------------- | ------------- | ------------- |
| **Monthly cost**      | $5-15         | $6 flat       | $0-14         | $12-25+       |
| **Setup difficulty**  | Easy          | Medium        | Easy          | Easy          |
| **WebSocket support** | ✅            | ✅            | ✅            | ✅ (paid)     |
| **Background tasks**  | ✅            | ✅            | Paid only     | Paid only     |
| **Managed DB/Redis**  | ✅ included   | ❌ self-host  | ✅ add-on     | ✅ add-on     |
| **Auto-deploy**       | ✅            | Manual/CI     | ✅            | ✅            |
| **SSL/HTTPS**         | ✅ auto       | Manual (Caddy)| ✅ auto       | ✅ auto       |
| **Docker Compose**    | Per-service   | ✅ native     | Per-service   | ❌            |
| **Scaling**           | Horizontal    | Vertical      | Horizontal    | Horizontal    |
| **Always-on**         | ✅            | ✅            | Paid only     | Paid only     |

**Verdict:** Railway for ease + cost balance. DO Droplet for cheapest flat rate with full control.

---

## Production Hardening Checklist

Before going live with real funds:

- [ ] Set `PHEMEX_TESTNET=false` only after thorough testing
- [ ] Generate a strong `JWT_SECRET` (64+ characters)
- [ ] Set `DEBUG=false`
- [ ] Update `CORS_ORIGINS` to your production domain only
- [ ] Set strong PostgreSQL password (not `postgres`)
- [ ] Enable database backups (daily)
- [ ] Set up monitoring/alerting (UptimeRobot free tier, or Render/Railway built-in)
- [ ] Configure rate limiting (`RATE_LIMIT_PER_MINUTE`)
- [ ] Remove any test API keys from frontend `.env`
- [ ] Review agent risk limits before enabling live trading
- [ ] Test stop-loss execution on testnet before production
- [ ] Set up log aggregation (Railway/Render have built-in logs)

---

## Quick Reference Commands

```bash
# Local development
docker compose up -d
docker compose logs -f backend

# Production (DO Droplet)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
# Tables are auto-created on startup; run seed once on first deploy:
docker compose exec backend python scripts/seed_production.py
docker compose logs -f --tail=50

# Database backup (DO Droplet)
docker compose exec db pg_dump -U postgres phemex_ai_trader > backup_$(date +%Y%m%d).sql

# Database restore
cat backup_20260407.sql | docker compose exec -T db psql -U postgres phemex_ai_trader

# View running services
docker compose ps

# Restart a single service
docker compose restart backend

# Check health
curl https://your-domain.com/api/health
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Backend can't connect to DB | Check `DATABASE_URL` uses `postgresql+asyncpg://` scheme |
| WebSocket disconnects | Ensure proxy passes `Upgrade` and `Connection` headers |
| Frontend shows blank page | Check `VITE_API_URL` is set to `/api` at build time |
| CORS errors | Update `CORS_ORIGINS` to include your frontend domain |
| Railway DB URL wrong scheme | Use reference variables: `postgresql+asyncpg://${PGUSER}:...` |
| Out of memory (1GB droplet) | Add 2GB swap, reduce uvicorn workers to 1 |
| Slow cold starts | Railway/Render free tiers sleep — upgrade to paid |
