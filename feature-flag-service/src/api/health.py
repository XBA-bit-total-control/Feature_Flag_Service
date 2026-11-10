import asyncio

from asyncpg import PostgresError
from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from pymongo import timeout
from pymongo.errors import PyMongoError
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import Executable

from src.database import engine, get_mongo_database
from src.models.experiment_report import ExperimentReport

router = APIRouter()


async def _postgres_value(query: Executable) -> int | None:
    try:
        async with asyncio.timeout(2), engine.connect() as connection:
            return await connection.scalar(query)
    except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
        return None


def _mongo_value(*, pending: bool = False) -> int | None:
    try:
        with timeout(2):
            database = get_mongo_database()
            if pending:
                return database.events.count_documents({"processed": False})
            return int(database.command("ping")["ok"])
    except (PyMongoError, OSError):
        return None


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Liveness probe для Kubernetes."""
    return {"status": "ok"}


@router.get("/health")
@router.get("/health/ready")
async def readiness(response: Response) -> dict[str, str | dict[str, str]]:
    """Проверить доступность PostgreSQL и MongoDB."""
    postgres, mongo = await asyncio.gather(
        _postgres_value(text("SELECT 1")), run_in_threadpool(_mongo_value),
    )
    checks = {
        "postgres": "ok" if postgres == 1 else "unavailable",
        "mongo": "ok" if mongo == 1 else "unavailable",
    }
    ready = all(value == "ok" for value in checks.values())
    response.status_code = 200 if ready else 503
    return {"status": "ok" if ready else "unavailable", "checks": checks}


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    """Вернуть базовые метрики в текстовом формате Prometheus."""
    pending, fallbacks = await asyncio.gather(
        run_in_threadpool(_mongo_value, pending=True),
        _postgres_value(select(func.count()).select_from(ExperimentReport).where(
            ExperimentReport.status == "completed", ExperimentReport.fallback_reason.is_not(None),
        )),
    )
    samples = (
        ("feature_flags_resolve_requests_total", "counter", request.app.state.resolve_requests),
        ("feature_flags_events_pending", "gauge", pending),
        ("feature_flags_llm_fallback_reports", "gauge", fallbacks),
    )
    body = "".join(
        f"# TYPE {name} {kind}\n{name} {value}\n"
        for name, kind, value in samples if value is not None
    )
    return Response(body, media_type="text/plain; version=0.0.4")
