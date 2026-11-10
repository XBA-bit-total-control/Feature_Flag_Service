from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from asyncpg import InvalidPasswordError
from sqlalchemy import text

from src import main
from src.api import health
from src.main import app


@pytest.mark.asyncio
async def test_postgres_probe_handles_driver_error(monkeypatch) -> None:
    """Сбой подключения asyncpg должен означать недоступность PostgreSQL."""
    monkeypatch.setattr(health, "engine", Mock(connect=Mock(side_effect=InvalidPasswordError())))
    assert await health._postgres_value(text("SELECT 1")) is None, (
        "Ошибка драйвера PostgreSQL должна давать недоступность, а не HTTP 500"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("postgres", "mongo", "expected"), [
    (1, 1, 200), (None, 1, 503), (1, None, 503),
])
async def test_health_distinguishes_liveness_and_readiness(monkeypatch, postgres, mongo, expected) -> None:
    """Проверить статус API при отказе каждой из баз данных."""
    postgres_probe = AsyncMock(return_value=postgres)
    mongo_probe = Mock(return_value=mongo)
    monkeypatch.setattr(health, "_postgres_value", postgres_probe)
    monkeypatch.setattr(health, "_mongo_value", mongo_probe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        live = await client.get("/api/health/live")
        assert live.status_code == 200, f"Liveness зависит от базы: {live.text}"
        assert not postgres_probe.called and not mongo_probe.called, "Liveness вызвала базы данных"
        ready = await client.get("/api/health/ready")
        legacy = await client.get("/api/health")
    assert ready.status_code == legacy.status_code == expected, (
        f"Ожидался HTTP {expected}, readiness вернула {ready.status_code} и {legacy.status_code}"
    )
    assert ready.json()["checks"] == {
        "postgres": "ok" if postgres else "unavailable",
        "mongo": "ok" if mongo else "unavailable",
    }, f"Неверно указана недоступная база: {ready.text}"


@pytest.mark.asyncio
async def test_metrics_count_resolve_and_omit_unavailable_samples(monkeypatch) -> None:
    """Проверить счётчик и частичную доступность метрик."""
    monkeypatch.setattr(health, "_postgres_value", AsyncMock(return_value=4))
    monkeypatch.setattr(health, "_mongo_value", Mock(return_value=None))
    monkeypatch.setattr(app.state, "resolve_requests", 0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invalid = await client.post("/resolve", json={})
        metrics = await client.get("/api/metrics")
    assert invalid.status_code == 422, f"Пустой resolve должен быть отклонён: {invalid.text}"
    assert metrics.status_code == 200, f"Частичная недоступность сорвала сбор метрик: {metrics.text}"
    assert "feature_flags_resolve_requests_total 1\n" in metrics.text, (
        f"Запрос resolve не посчитан: {metrics.text}"
    )
    assert "feature_flags_llm_fallback_reports 4\n" in metrics.text, (
        f"Число fallback-отчётов потеряно: {metrics.text}"
    )
    assert "feature_flags_events_pending" not in metrics.text, "Недоступная метрика должна отсутствовать"


@pytest.mark.asyncio
async def test_request_log_uses_route_template(monkeypatch) -> None:
    """Убедиться, что журнал не содержит ID и параметров из запроса."""
    log = Mock()
    monkeypatch.setattr(main, "log_event", log)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/api/experiments/9d9e3168-6485-489f-8ca6-9f0916d1960b?private=value")
    fields = log.call_args.kwargs
    assert fields["handler"] == "get_experiment" and fields["route"] == "/{experiment_id}", (
        f"В журнале ожидался шаблон маршрута без данных запроса: {fields}"
    )
