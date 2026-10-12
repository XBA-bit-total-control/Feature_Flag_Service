from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.database import async_session_factory
from src.services import flag_service
from src.utils.hashing import deterministic_hash, resolve_bucket

router = APIRouter()


class ResolveRequest(BaseModel):
    """Запрос для определения варианта."""

    flag_key: str = Field(..., description="Ключ feature-флага")
    user_id: str = Field(..., description="Идентификатор пользователя")


class ResolveResponse(BaseModel):
    """Ответ с определённым вариантом."""

    flag_key: str
    user_id: str
    variant_key: str


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_flag(data: ResolveRequest):
    """Определить вариант A/B теста для пользователя."""
    async with async_session_factory() as db:
        flag = await flag_service.get_flag_by_key(db, data.flag_key)
        if flag is None:
            raise HTTPException(status_code=404, detail="Flag not found")

        if not flag.enabled:
            return ResolveResponse(
                flag_key=data.flag_key,
                user_id=data.user_id,
                variant_key="control",
            )

        variants = await flag_service.get_variants(db, flag.id)
        if not variants:
            return ResolveResponse(
                flag_key=data.flag_key,
                user_id=data.user_id,
                variant_key="control",
            )

        bucket = deterministic_hash(data.user_id, data.flag_key, flag.salt)
        weights = [v.weight for v in variants]
        variant_index = resolve_bucket(bucket, weights)
        variant = variants[variant_index]

        return ResolveResponse(
            flag_key=data.flag_key,
            user_id=data.user_id,
            variant_key=variant.variant_key,
        )
