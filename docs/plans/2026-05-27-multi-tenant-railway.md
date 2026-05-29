# Multi-Tenant + Railway Deployment — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add Clerk-based authentication, tenant-scoped data isolation, exchange/LLM API key management per tenant, and deploy to Railway.

**Architecture:** Clerk handles auth (JWT validation in backend middleware, ClerkProvider in frontend). Every DB row gets a `tenant_id` from the Clerk user/org ID. API keys for exchanges and LLM providers move from ephemeral in-memory config to encrypted DB storage per tenant. Railway deployment uses managed Postgres, with the existing `$PORT` and `envsubst` patterns already partially in place.

**Tech Stack:** Clerk (auth), clerk-python (backend JWT validation), @clerk/clerk-react (frontend), Postgres (existing), Railway (deployment), cryptography (Fernet for at-rest key encryption), OpenAI-compatible protocol (for local LLM providers: Ollama, vLLM, llama.cpp, OpenCode, custom endpoints)

---

## Current State Summary

| Area | Status |
|------|--------|
| Auth | **None** — no middleware, no login, API wide open |
| Tenant isolation | **None** — `User` model exists but not enforced; single shared DB |
| Exchange keys | Stored in `app_settings` ephemeral singleton — lost on restart |
| LLM keys | Same — in `app_settings`, not in DB, not per-user. No local provider support (Ollama, vLLM, etc.) |
| Docker | docker-compose for local; backend Dockerfile has `$PORT` for Railway |
| Frontend auth | No Clerk, no login page, no route guards |

---

## Phase 1: Auth Foundation (Clerk)

### Task 1: Install Clerk dependencies

**Objective:** Add Clerk SDKs to both backend and frontend.

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `frontend/package.json`

**Step 1: Add clerk-python to backend**

```bash
cd backend
echo "clerk-python>=0.2.0" >> requirements.txt
pip install clerk-python
```

**Step 2: Add @clerk/clerk-react to frontend**

```bash
cd frontend
npm install @clerk/clerk-react
```

**Step 3: Verify installs**

```bash
cd backend && python -c "import clerk; print('OK')"
cd frontend && ls node_modules/@clerk/clerk-react/package.json
```

**Step 4: Commit**

```bash
git add backend/requirements.txt frontend/package.json frontend/package-lock.json
git commit -m "deps: add Clerk SDKs for auth (clerk-python, @clerk/clerk-react)"
```

---

### Task 2: Create Clerk auth dependency for FastAPI

**Objective:** Add a FastAPI dependency that validates Clerk session tokens from the `Authorization` header and extracts the `sub` (user ID) claim.

**Files:**
- Create: `backend/app/auth.py`
- Modify: `backend/app/config.py`

**Step 1: Add Clerk config to settings**

Append to `backend/app/config.py`:

```python
# Clerk auth
clerk_secret_key: str = ""
clerk_publishable_key: str = ""
clerk_jwks_url: str = "https://api.clerk.com"
```

**Step 2: Create the auth dependency**

Create `backend/app/auth.py`:

```python
"""Clerk JWT validation dependency for FastAPI."""
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from clerk import ClerkSDK
from app.config import settings
import logging

logger = logging.getLogger(__name__)

_clerk: ClerkSDK | None = None

def _get_clerk() -> ClerkSDK:
    global _clerk
    if _clerk is None:
        if not settings.clerk_secret_key:
            raise HTTPException(
                status_code=500,
                detail="CLERK_SECRET_KEY not configured",
            )
        _clerk = ClerkSDK(secret_key=settings.clerk_secret_key)
    return _clerk

security = HTTPBearer(auto_error=False)


async def get_current_user_id(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str:
    """Extract Clerk user ID from JWT in Authorization header.
    
    Returns the Clerk `sub` claim (user ID) if the token is valid.
    Raises 401 if no token or invalid token.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    token = credentials.credentials
    clerk = _get_clerk()

    try:
        # Clerk's Python SDK provides verify_token
        session = clerk.verify_token(token)
        user_id = session.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing sub claim",
            )
        return user_id
    except Exception as e:
        logger.warning(f"Token validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )


# Optional: allows unauthenticated access, returns None
async def get_optional_user_id(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> str | None:
    if credentials is None:
        return None
    try:
        return await get_current_user_id(request, credentials)
    except HTTPException:
        return None
```

**Step 3: Commit**

```bash
git add backend/app/auth.py backend/app/config.py
git commit -m "feat: add Clerk JWT validation FastAPI dependency"
```

---

### Task 3: Add ClerkProvider to frontend and login page

**Objective:** Wrap the app in `<ClerkProvider>`, add a sign-in page, and protect routes with `<SignedIn>` / `<SignedOut>`.

**Files:**
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/pages/SignInPage.tsx`
- Modify: `frontend/src/App.tsx`

**Step 1: Wrap main.tsx with ClerkProvider**

In `frontend/src/main.tsx`, import and wrap:

```tsx
import { ClerkProvider } from '@clerk/clerk-react';

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || '';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ClerkProvider publishableKey={PUBLISHABLE_KEY}>
      <App />
    </ClerkProvider>
  </React.StrictMode>
);
```

**Step 2: Create SignInPage**

Create `frontend/src/pages/SignInPage.tsx`:

```tsx
import { SignIn } from '@clerk/clerk-react';

export function SignInPage() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      minHeight: '100vh', background: 'var(--bg)',
    }}>
      <SignIn routing="path" path="/sign-in" signUpUrl="/sign-up" />
    </div>
  );
}
```

Also create `frontend/src/pages/SignUpPage.tsx` with `<SignUp>`.

**Step 3: Add auth gating to App.tsx**

```tsx
import { SignedIn, SignedOut, RedirectToSignIn } from '@clerk/clerk-react';
import { SignInPage } from './pages/SignInPage';
import { SignUpPage } from './pages/SignUpPage';

// In the route setup:
<Routes>
  <Route path="/sign-in" element={<SignInPage />} />
  <Route path="/sign-up" element={<SignUpPage />} />
  <Route path="/*" element={
    <>
      <SignedIn>
        {/* existing app layout + routes */}
      </SignedIn>
      <SignedOut>
        <RedirectToSignIn />
      </SignedOut>
    </>
  } />
</Routes>
```

**Step 4: Commit**

```bash
git add frontend/src/main.tsx frontend/src/pages/SignInPage.tsx frontend/src/pages/SignUpPage.tsx frontend/src/App.tsx
git commit -m "feat: add Clerk sign-in/sign-up pages and route protection"
```

---

### Task 4: Add auth token forwarding in API client

**Objective:** The frontend API client (axios) must attach the Clerk session token as `Authorization: Bearer <token>` on every request.

**Files:**
- Modify: `frontend/src/lib/api.ts`

**Step 1: Add request interceptor**

```tsx
import { useAuth } from '@clerk/clerk-react';

// Create a function that returns the configured axios instance
// with the Clerk token interceptor attached
export function useAuthenticatedApi() {
  const { getToken } = useAuth();

  api.interceptors.request.use(async (config) => {
    const token = await getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });

  return api;
}
```

But since the axios instance is a singleton, a simpler approach:

In `api.ts`, add after line 10:

```tsx
// Token injection — call setAuthTokenGetter before making authenticated requests
let _getToken: (() => Promise<string | null>) | null = null;

export function setAuthTokenGetter(getToken: () => Promise<string | null>) {
  _getToken = getToken;
}

api.interceptors.request.use(async (config) => {
  if (_getToken) {
    const token = await _getToken();
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
  }
  return config;
});
```

In `main.tsx` or a top-level component, call:

```tsx
const { getToken } = useAuth();
useEffect(() => {
  setAuthTokenGetter(() => getToken());
}, [getToken]);
```

**Step 2: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/main.tsx
git commit -m "feat: attach Clerk session token to all API requests"
```

---

## Phase 2: Multi-Tenant Data Model

### Task 5: Add `tenant_id` to all core tables

**Objective:** Every row in the DB must be scoped to a tenant (Clerk user ID). Add `tenant_id VARCHAR(255)` column.

**Files:**
- Create: `backend/alembic/versions/XXXX_add_tenant_id_to_all_tables.py`
- Modify: `backend/app/models/__init__.py`

**Step 1: Create migration**

```python
"""add tenant_id to all core tables

Revision ID: a1b2c3d4e5f6
"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'c8a1f3e7d4b2'  # adjust to current head

# All tables that need tenant scoping
TABLES = [
    "agents", "trades", "positions", "balances", "agent_signals",
    "agent_run_records", "agent_metric_records", "accumulation_config",
    "accumulation_execution_records", "grid_states", "users",
    "api_keys", "traders", "backtest_records", "whale_addresses",
    "whale_snapshots", "strategy_overrides", "analyst_reports",
    "portfolio_decisions", "risk_assessments", "execution_plans",
    "cio_reports", "agent_decisions", "daily_reports",
    "trader_legacies",
]

def upgrade():
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(255)")
        # Create index on tenant_id for every table
        op.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_tenant_id ON {table}(tenant_id)")


def downgrade():
    for table in TABLES:
        op.execute(f"DROP INDEX IF EXISTS idx_{table}_tenant_id")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS tenant_id")
```

**Step 2: Update models**

In `backend/app/models/__init__.py`, add to every model class:

```python
tenant_id = Column(String(255), nullable=True, index=True)
```

(Do this for: User, Agent, Trade, Position, Balance, AgentSignal, AgentRunRecord, AgentMetricRecord, AccumulationConfig, AccumulationExecutionRecord, GridState, Trader, BacktestRecord, WhaleAddress, StrategyOverride, ApiKey, DailyReport, AnalystReport, PortfolioDecision, RiskAssessmentRecord, ExecutionPlan, CIOReport, AgentDecision, TraderLegacy)

**Step 3: Commit**

```bash
git add backend/alembic/versions/ backend/app/models/__init__.py
git commit -m "feat: add tenant_id column and indexes to all core tables"
```

---

### Task 6: Update existing API endpoints to filter by tenant

**Objective:** All GET/POST/PUT/DELETE endpoints must scope data to `tenant_id`. Add `tenant_id=Depends(get_current_user_id)` to every route.

**Files:**
- Modify: `backend/app/api/routes/settings.py`
- Modify: `backend/app/api/routes/agents.py`
- Modify: `backend/app/api/routes/paper_trading.py`
- Modify: `backend/app/api/routes/traders.py`
- Modify: `backend/app/api/routes/accumulation.py`
- Modify: `backend/app/api/routes/automation.py`
- Modify: `backend/app/api/routes/fund.py`
- Modify: `backend/app/api/routes/trading.py`
- Modify: `backend/app/api/routes/whale.py`
- Modify: `backend/app/api/routes/strategies.py`
- Modify: `backend/app/api/routes/live_trading.py`

**Step 1: Add tenant filtering pattern**

For each route, add `tenant_id: str = Depends(get_current_user_id)` and filter queries:

Example for `GET /api/agents`:

```python
from app.auth import get_current_user_id

@router.get("")
async def get_agents(tenant_id: str = Depends(get_current_user_id)):
    async with get_async_session() as db:
        result = await db.execute(
            select(Agent).where(Agent.tenant_id == tenant_id)
        )
        return result.scalars().all()
```

Example for `POST /api/agents`:

```python
@router.post("")
async def create_agent(
    req: AgentCreateRequest,
    tenant_id: str = Depends(get_current_user_id),
):
    async with get_async_session() as db:
        agent = Agent(**req.model_dump(), tenant_id=tenant_id)
        db.add(agent)
        await db.commit()
        return agent
```

**Do this for all route files.** This is a mechanical change — every endpoint gets `tenant_id` dependency, every query adds `.where(Model.tenant_id == tenant_id)`, every insert sets `tenant_id`.

**Step 2: Commit**

```bash
git add backend/app/api/routes/
git commit -m "feat: scope all API endpoints to tenant_id via Clerk auth"
```

---

## Phase 3: Exchange API Key Management

### Task 7: Create TenantCredential model + encryption utility

**Objective:** A single `TenantCredential` model that stores encrypted exchange and LLM API keys per tenant, replacing the ephemeral `app_settings` pattern.

**Files:**
- Create: `backend/app/services/credential_service.py`
- Modify: `backend/app/models/__init__.py`

**Step 1: Create encryption utility**

Create `backend/app/services/credential_service.py`:

```python
"""Encrypted credential storage per tenant. Uses Fernet symmetric encryption."""
import os
from cryptography.fernet import Fernet
from app.database import get_async_session
from app.models import TenantCredential

# Key from env; generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
_ENCRYPTION_KEY = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
_fernet = Fernet(_ENCRYPTION_KEY.encode()) if _ENCRYPTION_KEY else None


def encrypt(value: str) -> str:
    if not _fernet:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY not set")
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not _fernet:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY not set")
    return _fernet.decrypt(value.encode()).decode()


async def save_credential(tenant_id: str, provider: str, key: str, value: str) -> None:
    """Upsert an encrypted credential for a tenant+provider+key."""
    async with get_async_session() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.provider == provider,
                TenantCredential.credential_key == key,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.encrypted_value = encrypt(value)
        else:
            row = TenantCredential(
                tenant_id=tenant_id,
                provider=provider,
                credential_key=key,
                encrypted_value=encrypt(value),
            )
            db.add(row)
        await db.commit()


async def get_credential(tenant_id: str, provider: str, key: str) -> str | None:
    async with get_async_session() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.provider == provider,
                TenantCredential.credential_key == key,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            return decrypt(row.encrypted_value)
    return None


async def delete_credential(tenant_id: str, provider: str, key: str) -> bool:
    async with get_async_session() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.provider == provider,
                TenantCredential.credential_key == key,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            await db.delete(row)
            await db.commit()
            return True
    return False


async def list_credentials(tenant_id: str, provider: str | None = None) -> list[dict]:
    """List credential keys for a tenant (values are never returned in lists)."""
    async with get_async_session() as db:
        from sqlalchemy import select
        q = select(TenantCredential).where(TenantCredential.tenant_id == tenant_id)
        if provider:
            q = q.where(TenantCredential.provider == provider)
        result = await db.execute(q)
        rows = result.scalars().all()
        return [
            {
                "provider": r.provider,
                "credential_key": r.credential_key,
                "has_value": bool(r.encrypted_value),
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]
```

**Step 2: Add TenantCredential model**

In `backend/app/models/__init__.py`:

```python
class TenantCredential(Base):
    """Encrypted key-value store for per-tenant credentials (exchange keys, LLM keys)."""
    __tablename__ = "tenant_credentials"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(String(255), nullable=False, index=True)
    provider = Column(String(50), nullable=False)   # "phemex", "hyperliquid", "openai", etc.
    credential_key = Column(String(100), nullable=False)  # "api_key", "api_secret", "wallet_key"
    encrypted_value = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_credential_tenant_provider", "tenant_id", "provider"),
        UniqueConstraint("tenant_id", "provider", "credential_key", name="uq_credential_tenant_provider_key"),
    )
```

**Step 3: Commit**

```bash
git add backend/app/services/credential_service.py backend/app/models/__init__.py
git commit -m "feat: add encrypted TenantCredential model and credential_service"
```

---

### Task 8: Create exchange credential API endpoints

**Objective:** Add PUT/GET/DELETE endpoints for per-tenant exchange API keys.

**Files:**
- Modify: `backend/app/api/routes/settings.py`

**Step 1: Add exchange credential endpoints**

```python
from app.auth import get_current_user_id
from app.services.credential_service import (
    save_credential, get_credential, delete_credential, list_credentials,
)
from pydantic import BaseModel

class ExchangeCredentialSaveRequest(BaseModel):
    provider: str  # "phemex", "hyperliquid", "alpaca"
    credentials: dict[str, str]  # e.g., {"api_key": "...", "api_secret": "..."}

class ExchangeCredentialResponse(BaseModel):
    provider: str
    keys: list[dict]  # [{credential_key, has_value}]


@router.put("/exchange-credentials")
async def save_exchange_credentials(
    req: ExchangeCredentialSaveRequest,
    tenant_id: str = Depends(get_current_user_id),
):
    """Save exchange API credentials for the current tenant."""
    if req.provider not in ("phemex", "hyperliquid", "alpaca"):
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    for key, value in req.credentials.items():
        if not value:
            continue
        await save_credential(tenant_id, req.provider, key, value)

    return {"status": "ok", "provider": req.provider}


@router.get("/exchange-credentials")
async def list_exchange_credentials(
    tenant_id: str = Depends(get_current_user_id),
):
    """List saved exchange credentials (values not shown)."""
    all_creds = await list_credentials(tenant_id)
    exchange_providers = {"phemex", "hyperliquid", "alpaca"}
    result = {}
    for cred in all_creds:
        if cred["provider"] in exchange_providers:
            if cred["provider"] not in result:
                result[cred["provider"]] = []
            result[cred["provider"]].append({
                "key": cred["credential_key"],
                "has_value": cred["has_value"],
            })
    return result


@router.delete("/exchange-credentials/{provider}")
async def delete_exchange_credentials(
    provider: str,
    tenant_id: str = Depends(get_current_user_id),
):
    """Delete all credentials for a specific exchange provider."""
    # Delete known keys for this provider
    known_keys = {
        "phemex": ["api_key", "api_secret"],
        "hyperliquid": ["wallet_address", "wallet_key"],
        "alpaca": ["api_key", "api_secret"],
    }
    for key in known_keys.get(provider, []):
        await delete_credential(tenant_id, provider, key)
    return {"status": "ok", "provider": provider}
```

**Step 2: Commit**

```bash
git add backend/app/api/routes/settings.py
git commit -m "feat: add exchange credential CRUD endpoints (PUT/GET/DELETE)"
```

---

### Task 9: Create exchange API key forms in frontend Settings

**Objective:** Replace the hardcoded Phemex-only API key form with a per-exchange credential form supporting Phemex, Hyperliquid, and Alpaca.

**Files:**
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/lib/api.ts`

**Step 1: Add API methods**

```tsx
// In settingsApi in api.ts:
saveExchangeCredentials: (provider: string, credentials: Record<string, string>) =>
  api.put('settings/exchange-credentials', { provider, credentials }),

listExchangeCredentials: () =>
  api.get('settings/exchange-credentials').then(r => r.data),

deleteExchangeCredentials: (provider: string) =>
  api.delete(`settings/exchange-credentials/${provider}`),
```

**Step 2: Replace API Keys tab content**

Replace the Phemex-only form with three collapsible sections:

- **Phemex** — API Key, API Secret, Testnet toggle
- **Hyperliquid** — Wallet Address, Private Key (masked), Testnet toggle
- **Alpaca** — API Key, API Secret, Paper/Live toggle

Each section shows:
- A status indicator (configured / not configured)
- Expand/collapse with "Configure" / "Edit" button
- Masked values when configured (show last 4 chars)
- Save and Clear buttons

**Step 3: Commit**

```bash
git add frontend/src/pages/SettingsPage.tsx frontend/src/lib/api.ts
git commit -m "feat: add multi-exchange credential forms to Settings"
```

---

## Phase 4: LLM API Key Management

### Task 10: Create LLM credential API endpoints

**Objective:** Add PUT/GET/DELETE endpoints for per-tenant LLM provider API keys. Supports cloud providers (OpenAI, Anthropic, OpenRouter, Azure, OpenCode) and local providers (Ollama, vLLM, llama.cpp, custom OpenAI-compatible endpoints). Local providers don't require API keys — just an endpoint URL. The `custom` provider is a catch-all for any OpenAI-compatible API (LM Studio, Groq, Together, etc.).

**Files:**
- Modify: `backend/app/api/routes/settings.py`

**Step 1: Add LLM credential endpoints (with endpoint_url support)**

```python
class LLMCredentialSaveRequest(BaseModel):
    provider: str  # "openai", "anthropic", "openrouter", "azure", "opencode",
                   # "ollama", "vllm", "llama_cpp", "custom"
    api_key: str | None = None       # cloud providers need this; local = None
    endpoint_url: str | None = None  # local/custom providers need this; cloud = None
    model_name: str | None = None    # optional default model for this provider

# Known providers and their credential requirements
LLM_PROVIDERS = {
    # Cloud — require api_key
    "openai":     {"needs_key": True,  "default_endpoint": "https://api.openai.com/v1"},
    "anthropic":  {"needs_key": True,  "default_endpoint": "https://api.anthropic.com/v1"},
    "openrouter": {"needs_key": True,  "default_endpoint": "https://openrouter.ai/api/v1"},
    "azure":      {"needs_key": True,  "default_endpoint": None},  # endpoint is user-provided
    "opencode":   {"needs_key": True,  "default_endpoint": "https://api.opencode.ai/v1"},
    # Local — no API key, just endpoint URL
    "ollama":     {"needs_key": False, "default_endpoint": "http://localhost:11434/v1"},
    "vllm":       {"needs_key": False, "default_endpoint": "http://localhost:8000/v1"},
    "llama_cpp":  {"needs_key": False, "default_endpoint": "http://localhost:8080/v1"},
    "custom":     {"needs_key": False, "default_endpoint": None},  # fully user-specified
}


@router.put("/llm-credentials")
async def save_llm_credential(
    req: LLMCredentialSaveRequest,
    tenant_id: str = Depends(get_current_user_id),
):
    """Save an LLM provider config for the current tenant."""
    if req.provider not in LLM_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown LLM provider: {req.provider}. Valid: {list(LLM_PROVIDERS.keys())}"
        )

    meta = LLM_PROVIDERS[req.provider]

    if meta["needs_key"] and not req.api_key:
        raise HTTPException(
            status_code=400,
            detail=f"Provider '{req.provider}' requires an API key"
        )

    # Persist: api_key, endpoint_url, model_name as separate credential keys
    if req.api_key:
        await save_credential(tenant_id, req.provider, "api_key", req.api_key)
    if req.endpoint_url or meta["default_endpoint"]:
        endpoint = req.endpoint_url or meta["default_endpoint"]
        await save_credential(tenant_id, req.provider, "endpoint_url", endpoint)
    if req.model_name:
        await save_credential(tenant_id, req.provider, "model_name", req.model_name)

    return {"status": "ok", "provider": req.provider}


@router.get("/llm-credentials")
async def list_llm_credentials(
    tenant_id: str = Depends(get_current_user_id),
):
    """List which LLM providers have configs (values not shown)."""
    all_creds = await list_credentials(tenant_id)
    llm_providers = set(LLM_PROVIDERS.keys())
    result = {}
    for cred in all_creds:
        if cred["provider"] in llm_providers:
            p = cred["provider"]
            if p not in result:
                result[p] = {}
            result[p]["has_key"] = result[p].get("has_key") or (
                cred["credential_key"] == "api_key" and cred["has_value"]
            )
            result[p]["has_endpoint"] = result[p].get("has_endpoint") or (
                cred["credential_key"] == "endpoint_url" and cred["has_value"]
            )
            result[p]["has_model"] = result[p].get("has_model") or (
                cred["credential_key"] == "model_name" and cred["has_value"]
            )
    # Fill in providers with no creds yet
    for p in llm_providers:
        if p not in result:
            result[p] = {"has_key": False, "has_endpoint": False, "has_model": False}
    return result


@router.delete("/llm-credentials/{provider}")
async def delete_llm_credential(
    provider: str,
    tenant_id: str = Depends(get_current_user_id),
):
    """Delete all stored credentials for an LLM provider."""
    if provider not in LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    for key in ("api_key", "endpoint_url", "model_name"):
        await delete_credential(tenant_id, provider, key)
    return {"status": "ok", "provider": provider}
```

**Step 2: Commit**

```bash
git add backend/app/api/routes/settings.py
git commit -m "feat: add LLM credential CRUD endpoints — cloud + local providers (ollama, vllm, llama_cpp, opencode, custom)"
```

---

### Task 11: Update LLM settings tab in frontend

**Objective:** The LLM tab in Settings should show per-provider cards — cloud providers (OpenAI, Anthropic, OpenRouter, Azure, OpenCode) requiring API keys, and local providers (Ollama, vLLM, llama.cpp, custom) requiring only an endpoint URL. The `custom` provider allows any OpenAI-compatible endpoint (LM Studio, Groq, Together, etc.).

**Files:**
- Modify: `frontend/src/pages/SettingsPage.tsx`
- Modify: `frontend/src/lib/api.ts`

**Step 1: Add API methods (with endpoint_url + model_name support)**

```tsx
saveLlmCredential: (provider: string, apiKey?: string, endpointUrl?: string, modelName?: string) =>
  api.put('settings/llm-credentials', {
    provider,
    api_key: apiKey || null,
    endpoint_url: endpointUrl || null,
    model_name: modelName || null,
  }),

listLlmCredentials: () =>
  api.get('settings/llm-credentials').then(r => r.data),

deleteLlmCredential: (provider: string) =>
  api.delete(`settings/llm-credentials/${provider}`),
```

**Step 2: Build LLM keys form — two groups (cloud + local)**

```
┌──────────────────────────────────────────────┐
│ LLM Configuration                            │
│                                              │
│ ── Cloud Providers ──────────────────────    │
│ ┌─ OpenAI ───────────────────────────────┐   │
│ │ Status: ✅ Configured (...sk-abc)       │   │
│ │ Model: gpt-4o        [Edit] [Clear]    │   │
│ └────────────────────────────────────────┘   │
│ ┌─ Anthropic ────────────────────────────┐   │
│ │ Status: ❌ Not configured              │   │
│ │ API Key: [____________] [Save]         │   │
│ └────────────────────────────────────────┘   │
│ ┌─ OpenRouter ───────────────────────────┐   │
│ │ Status: ✅ Configured (...xyz)          │   │
│ │ Model: openai/gpt-4o-mini [Edit][Clear]│   │
│ └────────────────────────────────────────┘   │
│ ┌─ Azure ────────────────────────────────┐   │
│ │ Status: ❌ Not configured              │   │
│ │ API Key: [____________]                 │   │
│ │ Endpoint: [____________] [Save]        │   │
│ └────────────────────────────────────────┘   │
│ ┌─ OpenCode ─────────────────────────────┐   │
│ │ Status: ❌ Not configured              │   │
│ │ API Key: [____________]                 │   │
│ │ Endpoint: https://api.opencode.ai/v1   │   │
│ │ Model: [___________] [Save]            │   │
│ └────────────────────────────────────────┘   │
│                                              │
│ ── Local Providers (no API key needed)       │
│ ┌─ Ollama ───────────────────────────────┐   │
│ │ Status: ❌ Not configured              │   │
│ │ Endpoint: [http://localhost:11434/v1]  │   │
│ │ Model: [llama3]            [Save]      │   │
│ └────────────────────────────────────────┘   │
│ ┌─ vLLM ─────────────────────────────────┐   │
│ │ Status: ❌ Not configured              │   │
│ │ Endpoint: [http://localhost:8000/v1]   │   │
│ │ Model: [___________]       [Save]      │   │
│ └────────────────────────────────────────┘   │
│ ┌─ llama.cpp ────────────────────────────┐   │
│ │ Status: ❌ Not configured              │   │
│ │ Endpoint: [http://localhost:8080/v1]   │   │
│ │ Model: [___________]       [Save]      │   │
│ └────────────────────────────────────────┘   │
│ ┌─ Custom (OpenAI-compatible) ───────────┐   │
│ │ Status: ❌ Not configured              │   │
│ │ Endpoint: [____________]               │   │
│ │ API Key: [__] (optional)               │   │
│ │ Model: [___________]       [Save]      │   │
│ └────────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
```

**Design notes:**
- Cloud providers: API key field is required, endpoint is auto-filled from defaults (editable only for Azure and OpenCode)
- Local providers: endpoint URL is required, API key field is hidden or labeled "optional"
- Each card shows a status dot: green (configured), gray (not configured)
- "Edit" toggles the card to edit mode; "Clear" removes all stored credentials
- Default endpoint URLs are pre-filled but editable
- The `custom` provider has both endpoint and optional API key fields — covers Groq, Together, LM Studio, etc.

**Step 3: Commit**

```bash
git add frontend/src/pages/SettingsPage.tsx frontend/src/lib/api.ts
git commit -m "feat: add cloud + local LLM provider forms in Settings (ollama, vllm, llama_cpp, opencode, custom)"
```

---

### Task 12: Wire credential_service into runtime

**Objective:** When the scheduler/trading service needs an API key or LLM endpoint, it reads from `credential_service` instead of `app_settings`. This makes keys tenant-aware and survive restarts. For local LLM providers, the runtime resolves the correct base URL and handles the optional API key.

**Files:**
- Modify: `backend/app/services/agent_scheduler.py`
- Modify: `backend/app/services/llm.py`
- Modify: `backend/app/clients/phemex.py`
- Modify: `backend/app/clients/hyperliquid.py`
- Modify: `backend/app/services/credential_service.py`

**Step 1: Create key-lookup helpers**

In `backend/app/services/credential_service.py`:

```python
async def get_exchange_keys(tenant_id: str, provider: str) -> dict[str, str]:
    """Fetch all credentials for an exchange provider as a flat dict."""
    keys = {}
    async with get_async_session() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.provider == provider,
            )
        )
        for row in result.scalars().all():
            keys[row.credential_key] = decrypt(row.encrypted_value)
    return keys


async def get_llm_config(tenant_id: str, provider: str) -> dict[str, str | None]:
    """Fetch LLM provider config: api_key, endpoint_url, model_name.
    
    Returns defaults if nothing is stored (e.g., cloud providers get their
    standard endpoint). Local providers return None for api_key."""
    config = await get_exchange_keys(tenant_id, provider)

    # Apply defaults from LLM_PROVIDERS registry
    from app.api.routes.settings import LLM_PROVIDERS
    meta = LLM_PROVIDERS.get(provider, {})
    if "endpoint_url" not in config and meta.get("default_endpoint"):
        config["endpoint_url"] = meta["default_endpoint"]

    return {
        "api_key": config.get("api_key"),
        "endpoint_url": config.get("endpoint_url"),
        "model_name": config.get("model_name"),
    }
```

**Step 2: Update scheduler to load per-tenant keys**

In `agent_scheduler.py`, when creating exchange clients, pass `tenant_id` and call `get_exchange_keys()`.

**Step 3: Update LLM service with provider routing**

In `llm.py`, replace the single `app_settings`-based provider dispatch with a tenant-aware router:

```python
async def get_llm_client(tenant_id: str, provider: str):
    """Return an OpenAI-compatible client configured for the given tenant+provider.
    
    Cloud providers (openai, anthropic, openrouter) use their SDKs.
    Local providers (ollama, vllm, llama_cpp) and custom use an OpenAI-compatible
    client pointed at the configured endpoint_url."""
    from app.services.credential_service import get_llm_config

    config = await get_llm_config(tenant_id, provider)

    if provider in ("openai", "openrouter", "opencode", "azure"):
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=config["api_key"],
            base_url=config["endpoint_url"],
        )
    elif provider == "anthropic":
        from anthropic import AsyncAnthropic
        return AsyncAnthropic(api_key=config["api_key"])
    else:
        # Local providers + custom: all use OpenAI-compatible protocol
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            api_key=config.get("api_key") or "not-needed",  # local providers don't care
            base_url=config["endpoint_url"],
        )


async def get_llm_model(tenant_id: str, provider: str, fallback: str) -> str:
    """Get the configured model name for a provider, or the fallback."""
    config = await get_llm_config(tenant_id, provider)
    return config.get("model_name") or fallback
```

This means any local OpenAI-compatible endpoint (Ollama, vLLM, llama.cpp, LM Studio, Groq, Together) works with zero special-casing — just set the endpoint URL and optional API key.

**Step 4: Commit**

```bash
git add backend/app/services/credential_service.py backend/app/services/agent_scheduler.py backend/app/services/llm.py
git commit -m "feat: wire credential_service into trading + LLM runtime with local/custom provider routing"
```

---

## Phase 5: Railway Deployment

### Task 13: Create railway.json service config

**Objective:** Define the Railway service configuration for backend + frontend.

**Files:**
- Create: `railway.json`

**Step 1: Create railway.json**

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {
    "builder": "NIXPACKS",
    "buildCommand": "cd backend && pip install -r requirements.txt"
  },
  "deploy": {
    "startCommand": "cd backend && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}",
    "healthcheckPath": "/api/health",
    "healthcheckTimeout": 30,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 3
  }
}
```

Actually, Railway uses `nixpacks.toml` or `railway.toml`. Let's use the correct format:

Create `backend/nixpacks.toml`:

```toml
[phases.setup]
nixPkgs = ["python311", "gcc"]

[phases.install]
cmds = ["pip install -r requirements.txt"]

[start]
cmd = "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"
```

**Step 2: Add health check endpoint**

In `backend/app/main.py`:

```python
@api_router.get("/health")
async def health_check():
    return {"status": "ok", "version": settings.app_version}
```

**Step 3: Commit**

```bash
git add backend/nixpacks.toml backend/app/main.py
git commit -m "feat: add Railway nixpacks config and health check endpoint"
```

---

### Task 14: Add Railway env var template and docs

**Objective:** Document all required environment variables for Railway deployment.

**Files:**
- Modify: `backend/app/config.py`
- Create: `RAILWAY.md`

**Step 1: Ensure config reads Railway Postgres vars**

Already partly done — `config.py` has `resolved_database_url` that handles `PGHOST`, `PGPORT`, etc. Verify these are read:

```python
# These must be added to the Settings class:
pghost: Optional[str] = None
pgport: Optional[str] = None
pguser: Optional[str] = None
pgpassword: Optional[str] = None
pgdatabase: Optional[str] = None
```

They are already in `config.py` — good.

**Step 2: Create RAILWAY.md**

```markdown
# Railway Deployment

## Required Environment Variables

| Variable | Source | Notes |
|----------|--------|-------|
| `DATABASE_URL` | Railway Postgres | Auto-provided if using Railway Postgres plugin |
| `PGHOST` | Railway Postgres | Fallback if DATABASE_URL not set |
| `PGPORT` | Railway Postgres | |
| `PGUSER` | Railway Postgres | |
| `PGPASSWORD` | Railway Postgres | |
| `PGDATABASE` | Railway Postgres | |
| `CLERK_SECRET_KEY` | Clerk Dashboard | Secret key for JWT validation |
| `CLERK_PUBLISHABLE_KEY` | Clerk Dashboard | Public key for frontend |
| `CREDENTIAL_ENCRYPTION_KEY` | Self-generated | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `MAIL_SERVER_API_KEY` | Mailgun/Resend | Optional — email reports |
| `MAIL_TO_ADDRESS` | You | Optional |
| `MAIL_FROM_ADDRESS` | You | Optional |
| `TELEGRAM_BOT_TOKEN` | BotFather | Optional |
| `REDIS_URL` | Railway Redis | Optional — for caching |

## Frontend Env Vars (build-time)

| Variable | Value |
|----------|-------|
| `VITE_CLERK_PUBLISHABLE_KEY` | Same as CLERK_PUBLISHABLE_KEY |
| `VITE_API_URL` | `/api` (proxied via nginx) |
| `BACKEND_HOST` | `backend.railway.internal` |

## Steps

1. Create new Railway project
2. Add Postgres plugin
3. Add backend service (from `backend/` directory, nixpacks builder)
4. Add all env vars from above
5. Add frontend service (from `frontend/` directory) with nginx
6. Link services via Railway internal networking
7. Set Clerk redirect URLs to Railway domain
```

**Step 3: Commit**

```bash
git add RAILWAY.md backend/app/config.py
git commit -m "docs: add Railway deployment guide and env var documentation"
```

---

### Task 15: Update nginx config for Railway internal networking

**Objective:** The frontend nginx should proxy `/api/` to the backend service via Railway's internal DNS.

**Files:**
- Modify: `frontend/nginx.conf`

**Step 1: Update nginx.conf**

```nginx
server {
    listen 3000;
    server_name _;

    # API proxy to backend
    location /api/ {
        proxy_pass http://${BACKEND_HOST}:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    # WebSocket proxy
    location /api/ws/ {
        proxy_pass http://${BACKEND_HOST}:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400s;
    }

    # Frontend static files
    location / {
        root /usr/share/nginx/html;
        index index.html;
        try_files $uri $uri/ /index.html;
    }
}
```

The frontend Dockerfile already uses `envsubst` to expand `${BACKEND_HOST}`, so this will work with `BACKEND_HOST=backend.railway.internal` on Railway.

**Step 2: Commit**

```bash
git add frontend/nginx.conf
git commit -m "feat: update nginx config for Railway internal service routing"
```

---

### Task 16: Add cryptography dependency

**Objective:** The credential service needs `cryptography` for Fernet encryption.

**Files:**
- Modify: `backend/requirements.txt`

**Step 1: Add dependency**

```bash
echo "cryptography>=41.0.0" >> backend/requirements.txt
```

**Step 2: Commit**

```bash
git add backend/requirements.txt
git commit -m "deps: add cryptography for credential encryption"
```

---

## Execution Order

```
Task 1  → Install Clerk deps
Task 2  → Auth dependency (backend)
Task 3  → ClerkProvider + sign-in (frontend)
Task 4  → Token forwarding in API client
Task 5  → tenant_id migration
Task 6  → Scoped API endpoints
Task 7  → Credential model + encryption
Task 8  → Exchange credential endpoints
Task 9  → Exchange key forms (frontend)
Task 10 → LLM credential endpoints
Task 11 → LLM key forms (frontend)
Task 12 → Wire credentials into runtime
Task 13 → Railway nixpacks config
Task 14 → Env var docs
Task 15 → Nginx Railway config
Task 16 → cryptography dep
```

Tasks 1–4 are sequential (auth foundation). Tasks 5–6 depend on auth. Tasks 7–12 depend on 5 (tenant_id exists). Tasks 9 and 11 can be parallelized if using subagents. Tasks 13–16 are parallelizable.

---

## Verification Checklist

- [ ] `GET /api/settings` returns 401 without token
- [ ] `GET /api/settings` returns settings scoped to tenant with valid token
- [ ] Sign-in page renders with Clerk UI
- [ ] After sign-in, API calls include `Authorization: Bearer ***
- [ ] Exchange credentials survive backend restart
- [ ] LLM credentials survive backend restart (both cloud and local providers)
- [ ] Two different Clerk users see different agents/positions/trades
- [ ] Ollama provider: configure endpoint → LLM calls route to local Ollama
- [ ] Custom provider: configure arbitrary OpenAI-compatible endpoint → LLM calls succeed
- [ ] OpenCode provider: configure API key + endpoint → LLM calls route correctly
- [ ] Two different tenants can use different LLM providers simultaneously
- [ ] Railway build succeeds with nixpacks
- [ ] Health check returns 200 on Railway deploy
- [ ] Frontend proxies API correctly on Railway