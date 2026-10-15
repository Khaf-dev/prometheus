"""
Auth management endpoints.

Khusus untuk manage API keys — create, list, revoke.
Route ini sendiri dilindungi oleh auth (kecuali setup awal).

Bootstrap flow untuk first-time setup:
  POST /api/v1/auth/setup  →  tidak perlu auth, hanya bisa dipakai sekali
                               (jika sudah ada key, endpoint ini ditolak)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated
from fastapi import Depends

from app.auth.crypto import generate_api_key, hash_api_key
from app.auth.middleware import AuthDep
from app.auth.models import ApiKey
from app.core.database import get_db_session

router = APIRouter(prefix="/auth", tags=["auth"])

DbDep = Annotated[AsyncSession, Depends(get_db_session)]

# ─── Schemas ──────────────────────────────────────────────────────────────────

class SetupRequest(BaseModel):
    key_name: str = "default"   #nama device/client


class SetupResponse(BaseModel):
    """
    Response setup awal — plain key HANYA tampil sekali di sini.
    Setelah ini tidak bisa di-recover, harus buat key baru kalau hilang.
    """
    key_id: int
    key_name: str
    api_key: str        # <- harus disimpan baik-baik
    key_preview: str
    message: str



class CreateKeyRequest(BaseModel):
    key_name: str


class CreateKeyResponse(BaseModel):
    key_id: int
    key_name: str
    api_key: str        # plain key, hanya tampil sekali
    key_preview: str


class ApiKeyInfo(BaseModel):
    id: int
    name: str
    key_preview: str
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime


class RevokeResponse(BaseModel):
    message: str
    key_id: int



# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/setup", response_model=SetupResponse, status_code=status.HTTP_201_CREATED)
async def setup_first_key(request: SetupRequest, db: DbDep):
    """
    Bootstrap endpoint — buat API key pertama kali.
    Hanya bisa dipanggil jika BELUM ADA key di database.

    Setelah key pertama dibuat, endpoint ini akan menolak request
    (gunakan POST /auth/keys dengan auth untuk key berikutnya).

    Flow pertama kali:
        1. Jalankan server
        2. POST /api/v1/auth/setup {"key_name": "laptop"}
        3. Copy api_key yang muncul → simpan di password manager
        4. Gunakan key itu untuk semua request berikutnya
    """

    # Cek apakah sudah adad key - kalau sudah ada maka akan ditolak untuk generate key baru
    existing_count = await db.scalar(select(func.count(ApiKey.id)))
    if existing_count and existing_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Setup sudah pernah dilakukan!."
                "Gunakan POST /api/v1/auth/keys (dengan auth) untuk buat key baru."
            ),
        )


    plain_key = generate_api_key()
    key_preview = f"{plain_key[:12]}...{plain_key[-4:]}"

    new_key = ApiKey(
        name=request.key_name,
        key_hash=hash_api_key(plain_key),
        key_preview=key_preview,
        is_active=True,
    )
    db.add(new_key)
    await db.flush()        # dapat id sebelum commit


    return SetupResponse(
        key_id=new_key.id,
        key_name=new_key.name,
        api_key=plain_key,
        key_preview=key_preview,
        message=(
            "⚠️  Simpan API key ini sekarang — tidak akan bisa ditampilkan lagi. "
            "Gunakan sebagai: Authorization: Bearer <api_key>"
        ),
    )


@router.post("/keys", response_model=CreateKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_key(
    request: CreateKeyRequest,
    _: AuthDep,      # membuktikan caller sudah atuh, tapi tidak dipakai
    db: DbDep,
):
    """Buat API key tambahan (misal: untuk device lain)"""
    plain_key = generate_api_key()
    key_preview = f"{plain_key[:12]}...{plain_key[-4:]}"


    new_key = ApiKey(
        name=request.key_name,
        key_hash=hash_api_key(plain_key),
        key_preview=key_preview,
        is_active=True,
    )
    db.add(new_key)
    await db.flush()


    return CreateKeyResponse(
        key_id=new_key.id,
        key_name=new_key.name,
        api_key=plain_key,
        key_preview=key_preview,
    )


@router.get("/keys", response_model=list[ApiKeyInfo])
async def list_keys(_: AuthDep, db: DbDep):
    """List semua API keys (hanya preview, bukan plain key)"""
    result = await db.execute(
        select(ApiKey).order_by(ApiKey.created_at.desc())
    )
    keys = result.scalars().all()
    return [
        ApiKeyInfo(
            id=k.id,
            name=k.name,
            key_preview=k.key_preview,
            is_active=k.is_active,
            last_used_at=k.last_used_at,
            created_at=k.created_at,
        )
        for k in keys
    ]


@router.delete("/keys/{key_id}", response_model=RevokeResponse)
async def revoke_key(key_id: int, auth_key: AuthDep, db: DbDep):
    """
    Revoke (nonaktifkan) API key.
    Tidak bisa revoke key yang sedang dipakai untuk request ini
    — harus ada minimal satu key aktif.
    """
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    key = result.scalar_one_or_none()

    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Key tidak ditemukan.")

    # Prevent self-revoke (jangan sampai lock diri sendiri)
    if key.id == auth_key.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tidak bisa me-revoke key yang sedang dipakai. Buat key baru dulu, lalu revoke ini.",
        )


    key.is_active = False
    return RevokeResponse(message=f"Key '{key.name}' berhasil direvoke.", key_id=key_id)