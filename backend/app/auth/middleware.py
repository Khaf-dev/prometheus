"""
FastAPI auth dependency — verifikasi API key di setiap request.

Pattern: HTTPBearer security scheme.
Client kirim key via header: Authorization: Bearer prom_xxxxx

Kenapa header, bukan query param?
- Header tidak ke-log di access log server/proxy secara default
- Tidak ke-cache oleh browser
- Tidak muncul di browser history / URL bar
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.crypto import verify_api_key
from app.auth.models import ApiKey
from app.core.database import get_db_session

_bearer_scheme = HTTPBearer(
    scheme_name="API Key",
    description="Masukkan API key Prometheus. Format: `prom_xxxxx`",
)

async def verify_request(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(_bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiKey:
    """
    Dependency utama auth — inject ke route manapun yang butuh proteksi.

    Flow:
    1. Ambil token dari Authorization header
    2. Query semua active keys dari DB
    3. Verify bcrypt — O(n) tapi n selalu kecil (personal tool, max ~5 keys)
    4. Update last_used_at
    5. Return ApiKey object (bisa dipakai di route untuk audit)

    Raises 401 untuk semua error — tidak expose info apakah key exist atau tidak.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="API key tidak valid atau tidak aktif.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    plain_key = credentials.credentials

    # Basic format check - hindari bcrypt call yang sia-sia
    if not plain_key.startswith("prom_"):
        raise unauthorized

    # Ambil semua active keys - untuk single-user ini selalu < 10 rows
    result = await db.execute(
        select(ApiKey).where(ApiKey.is_active == True) # noqa: E712
    )
    active_keys = result.scalars().all()


    if not active_keys:
        raise unauthorized

    # Cari key yang match - bcrypt verify O(n)
    matched_key: ApiKey | None = None
    for key_record in active_keys:
        if verify_api_key(plain_key, key_record.key_hash):
            matched_key = key_record
            break

    if matched_key is None:
        raise unauthorized


    # Update last_used_at tanpa blocking - fire and forget style
    await db.execute(
        update(ApiKey)
        .where(ApiKey.id == matched_key.id)
        .values(last_used_at=datetime.now(timezone.utc))
    )

    return matched_key


# Type alias untuk DI yang clean di routes
AuthDep = Annotated[ApiKey, Depends(verify_request)]