import json

import httpx
import pytest

from src.config import Settings
from src.services.report_service import (
    ReportContext, build_report_data, generate_report, template_report,
)
from src.services.statistics_service import VariantCounts, compare_conversions


@pytest.fixture
def context() -> ReportContext:
    """Подготовить готовые метрики без внешних запросов."""
    control, treatment = VariantCounts(1000, 100), VariantCounts(1000, 150)
    return ReportContext(
        "Checkout", "purchase", "A", "B", control, treatment,
        compare_conversions(control, treatment),
        alpha=0.05, min_sample_size=500,
    )


def config(**overrides) -> Settings:
    """Создать настройки с фиктивным ключом и без чтения .env."""
    return Settings(_env_file=None, **{
        "LLM_API_KEY": "fake", "LLM_API_BASE_URL": "https://example.test/v1",
        "LLM_MODEL": "test", "LLM_MAX_RETRIES": 0,
        "LLM_MAX_OUTPUT_TOKENS": 128, **overrides,
    })


@pytest.mark.asyncio
async def test_llm_success_ignores_reasoning_and_limits_output(context) -> None:
    """Клиент берёт только content и отправляет ограничение генерации."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {
                "content": "Краткий отчёт", "reasoning_content": "private reasoning",
            }}], "usage": {"total_tokens": 123},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await generate_report(context, config(), client)
    assert result.content == "Краткий отчёт" and result.report_type == "llm", (
        f"Ожидался текст content, получено {result}"
    )
    assert result.tokens_used == 123, "Не сохранён расход токенов из usage"
    body = json.loads(requests[0].content)
    assert body["max_completion_tokens"] == 128, "Не передан лимит генерации"
    facts = json.loads(body["messages"][1]["content"])
    assert facts == json.loads(json.dumps(build_report_data(context))), (
        "В запросе должны быть структурированные метрики без шаблонного отчёта"
    )
    assert facts["comparison"]["alpha"] == 0.05, "Потерян уровень значимости"
    assert facts["comparison"]["min_sample_size_per_group"] == 500, (
        "Потерян порог выборки для каждой группы"
    )
    assert "следующие действия" in body["messages"][0]["content"], (
        "Запрос к модели должен требовать рекомендации по действиям"
    )
    assert str(requests[0].url) == "https://example.test/v1/chat/completions", (
        "Клиент сформировал неверный URL"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    httpx.Response(401), httpx.Response(429), httpx.Response(500),
    httpx.Response(200, text="invalid json"),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": ""}}]}),
    httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}),
])
async def test_provider_errors_use_template(context, response) -> None:
    """Ошибки API и незавершённые ответы дают шаблонный отчёт."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: response)
    ) as client:
        result = await generate_report(context, config(), client)
    assert result.report_type == "template" and result.content == template_report(context), (
        f"Ожидался шаблон при ошибке ответа, получено {result}"
    )


@pytest.mark.asyncio
async def test_missing_key_sends_no_request(context) -> None:
    """Без ключа HTTP-запрос не отправляется."""

    def handler(request):
        pytest.fail("Без ключа вызван внешний API")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await generate_report(context, config(LLM_API_KEY=None), client)
    assert result.fallback_reason == "missing_key", "Не указана причина fallback"


@pytest.mark.asyncio
async def test_transport_failure_retries_are_bounded(context) -> None:
    """При тайм-ауте выполняется только заданное число повторов."""
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("test timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await generate_report(context, config(LLM_MAX_RETRIES=1), client)
    assert calls == 2, f"Ожидалась попытка и один повтор, получено {calls}"
    assert result.report_type == "template", "При тайм-ауте не сформирован шаблон"


def test_template_handles_empty_data() -> None:
    """Пустая выборка даёт понятное сообщение без выдуманного вывода."""
    empty = VariantCounts(0, 0)
    report = template_report(ReportContext(
        "Empty", "buy", "A", "B", empty, empty, compare_conversions(empty, empty)
    ))
    assert "Данных недостаточно" in report, "Нет пояснения недостаточной выборки"
    assert "Повторные просмотры p-value" in report, "Пропала оговорка об интерпретации p-value"


@pytest.mark.asyncio
async def test_transient_status_can_recover(context) -> None:
    """Временная ошибка повторяется и может завершиться успешным отчётом."""
    responses = iter([
        httpx.Response(500),
        httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": "Отчёт"},
        }]}),
    ])
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: next(responses))
    ) as client:
        result = await generate_report(context, config(LLM_MAX_RETRIES=1), client)
    assert result.report_type == "llm", "После успешного повтора остался fallback"


def test_legacy_context_keeps_unknown_alpha() -> None:
    """Старый снимок не получает выдуманный порог значимости."""
    empty = VariantCounts(0, 0)
    context = ReportContext(
        "Legacy", "buy", "A", "B", empty, empty, compare_conversions(empty, empty)
    )
    facts = build_report_data(context)
    assert facts["comparison"]["alpha"] is None, "Неизвестный alpha был подменён"
    assert facts["comparison"]["enough_data"] is False, (
        "Пустая выборка ошибочно отмечена достаточной"
    )
    assert all(value is None for value in facts["available_context"].values()), (
        "Неизвестный бизнес-контекст должен оставаться явно неизвестным"
    )
