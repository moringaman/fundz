"""Clerk JWT validation dependency for FastAPI.

Validates Clerk session tokens from the ``Authorization: Bearer *** header
and extracts the ``sub`` (user ID) claim for tenant scoping.

Uses PyJWT's ``PyJWKClient`` to fetch and cache Clerk's JWKS.
"""

from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

import logging

logger = logging.getLogger(__name__)

# PyJWKClient handles fetching and caching the JWKS automatically
_jwks_url = (settings.clerk_jwks_url or "").replace("/.well-known/jwks.json", "")
if not _jwks_url:
    _jwks_url = "https://divine-barnacle-59.clerk.accounts.dev"

_jwks_client = jwt.PyJWKClient(
    f"{_jwks_url}/.well-known/jwks.json",
    cache_keys=True,
    lifespan=3600,  # 1-hour cache
)

security = HTTPBearer(auto_error=False)


async def get_current_user_id(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """FastAPI dependency: extract the Clerk user ID from the Bearer token.

    Returns the ``sub`` claim (Clerk user ID) on success.
    Raises ``401`` if the token is missing, expired, or invalid.
    """
    if credentials is None:
        logger.warning("Auth rejected: missing Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    token = credentials.credentials

    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            key=signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing 'sub' claim",
            )
        logger.debug("Auth success: user=%s", user_id)
        return user_id

    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError as e:
        logger.warning("JWT validation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected auth error: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
        )


async def get_optional_user_id(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[str]:
    """Same as ``get_current_user_id`` but returns ``None`` for anonymous access."""
    if credentials is None:
        return None
    try:
        return await get_current_user_id(request, credentials)
    except HTTPException:
        return None
