from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    """Проверка работоспособности сервиса."""
    return {"status": "ok"}


@router.get("/health/live")
async def liveness() -> dict:
    """Liveness probe для Kubernetes."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness() -> dict:
    """Readiness probe для Kubernetes."""
    return {"status": "ok"}
