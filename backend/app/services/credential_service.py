"""Encrypted credential storage per tenant using Fernet symmetric encryption.

Credentials (exchange API keys, LLM API keys, endpoint URLs) are stored
encrypted in the ``tenant_credentials`` table. The encryption key must be
set via the ``CREDENTIAL_ENCRYPTION_KEY`` environment variable.

Generate a key:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.database import get_async_session
from app.models import TenantCredential

_ENCRYPTION_KEY = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
_fernet: Optional[Fernet] = Fernet(_ENCRYPTION_KEY.encode()) if _ENCRYPTION_KEY else None


def _encrypt(value: str) -> str:
    if not _fernet:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY not set in environment")
    return _fernet.encrypt(value.encode()).decode()


def _decrypt(value: str) -> str:
    if not _fernet:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY not set in environment")
    return _fernet.decrypt(value.encode()).decode()


# ── Core CRUD ─────────────────────────────────────────────────────────────────

async def save_credential(
    tenant_id: str, provider: str, key: str, value: str,
) -> None:
    """Upsert an encrypted credential for a tenant+provider+key."""
    async with get_async_session() as db:
        result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.provider == provider,
                TenantCredential.credential_key == key,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            row.encrypted_value = _encrypt(value)
        else:
            row = TenantCredential(
                tenant_id=tenant_id,
                provider=provider,
                credential_key=key,
                encrypted_value=_encrypt(value),
            )
            db.add(row)
        await db.commit()


async def get_credential(
    tenant_id: str, provider: str, key: str,
) -> Optional[str]:
    """Fetch and decrypt a single credential. Returns None if not found."""
    async with get_async_session() as db:
        result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.provider == provider,
                TenantCredential.credential_key == key,
            )
        )
        row = result.scalar_one_or_none()
        if row:
            return _decrypt(row.encrypted_value)
    return None


async def delete_credential(
    tenant_id: str, provider: str, key: str,
) -> bool:
    """Delete a single credential. Returns True if something was deleted."""
    async with get_async_session() as db:
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


async def list_credentials(
    tenant_id: str, provider: Optional[str] = None,
) -> list[dict]:
    """List credential keys for a tenant (values are never returned)."""
    async with get_async_session() as db:
        q = select(TenantCredential).where(
            TenantCredential.tenant_id == tenant_id,
        )
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


# ── Convenience helpers ───────────────────────────────────────────────────────

async def get_exchange_keys(
    tenant_id: str, provider: str,
) -> dict[str, str]:
    """Fetch all credentials for an exchange provider as a flat dict."""
    keys = {}
    async with get_async_session() as db:
        result = await db.execute(
            select(TenantCredential).where(
                TenantCredential.tenant_id == tenant_id,
                TenantCredential.provider == provider,
            )
        )
        for row in result.scalars().all():
            keys[row.credential_key] = _decrypt(row.encrypted_value)
    return keys


async def get_llm_config(
    tenant_id: str, provider: str,
) -> dict[str, Optional[str]]:
    """Fetch LLM provider config: api_key, endpoint_url, model_name.

    Returns *api_key*, *endpoint_url*, and *model_name* (each None if not set).
    """
    all_keys = await get_exchange_keys(tenant_id, provider)
    return {
        "api_key": all_keys.get("api_key"),
        "endpoint_url": all_keys.get("endpoint_url"),
        "model_name": all_keys.get("model_name"),
    }
