import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from pymongo import MongoClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import settings
from src.database import get_async_session
from src.main import app
from src.models.experiment import Experiment, ExperimentStatus
from src.models.experiment_report import ExperimentReport
from src.models.experiment_result import ExperimentResult
from src.models.flag import Flag
from src.models.flag_variant import FlagVariant
from src.services import report_service
from src.services.report_service import GeneratedReport, ReportContext
from src.services.statistics_service import VariantCounts, compare_conversions
from src.tasks import report_tasks, statistics_tasks


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
        reason="Нужна явно включённая проверка локальной PostgreSQL",
    ),
]


@pytest_asyncio.fixture
async def task_database(
    experiment_db: tuple[UUID, UUID, UUID], monkeypatch: pytest.MonkeyPatch,
) -> tuple[async_sessionmaker[AsyncSession], AsyncMock]:
    """Направить сессии задач в изолированную схему PostgreSQL текущего теста."""
    async for db in app.dependency_overrides[get_async_session]():
        sessions = async_sessionmaker(db.bind, expire_on_commit=False, join_transaction_mode="create_savepoint")
    dispose = AsyncMock()
    engine = Mock(dispose=dispose)
    for module in (report_tasks, statistics_tasks):
        monkeypatch.setattr(module, "create_async_engine", Mock(return_value=engine))
        monkeypatch.setattr(module, "async_sessionmaker", Mock(return_value=sessions))
    return sessions, dispose


@pytest.mark.skipif(
    os.environ.get("RUN_MONGO_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка MongoDB",
)
async def test_statistics_worker_calculates_running_experiments_and_saves_one_report(
    experiment_db: tuple[UUID, UUID, UUID], task_database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пересчитать запущенные эксперименты и сохранить шаблон только для значимого результата."""
    _, owner_id, _ = experiment_db
    sessions, dispose = task_database
    experiments = {}
    async with sessions.begin() as db:
        for name in ("significant", "balanced", "empty", "conflict", "paused"):
            flag = Flag(key=f"flag-{name}", owner_id=owner_id)
            db.add(flag)
            await db.flush()
            experiment = Experiment(
                name=name, primary_metric="buy", flag_id=flag.id, creator_id=owner_id,
                status=ExperimentStatus.PAUSED if name == "paused" else ExperimentStatus.RUNNING,
            )
            db.add(experiment)
            if name != "conflict":
                db.add_all([FlagVariant(flag_id=flag.id, variant_key=key) for key in ("A", "B")])
            experiments[name] = experiment

    with MongoClient(host=settings.MONGO_HOST, port=settings.MONGO_PORT,
                     serverSelectionTimeoutMS=3000) as client:
        mongo = client[f"codex_task_statistics_{uuid4().hex}"]
        monkeypatch.setattr(statistics_tasks, "get_mongo_database", lambda: mongo)
        generate = AsyncMock(side_effect=AssertionError("Автоматический отчёт не должен вызывать LLM"))
        monkeypatch.setattr(report_service, "generate_report", generate)
        try:
            timestamp = datetime.now(timezone.utc)
            events = []
            for name, conversions in (("significant", (10, 30)), ("balanced", (20, 20))):
                for key, successes in zip(("A", "B"), conversions):
                    for index in range(100):
                        event = {
                            "experiment_id": str(experiments[name].id), "flag_key": f"flag-{name}",
                            "variant_key": key, "user_id": f"{key}-{index}", "timestamp": timestamp,
                        }
                        events.append({**event, "event_type": "impression"})
                        if index < successes:
                            events.append({**event, "event_type": "conversion", "event_name": "buy"})
            mongo.events.insert_many(events)

            outcomes = await statistics_tasks._calculate_running()
            repeated = await statistics_tasks._calculate_running()
            expected = {
                str(experiments[name].id): "calculated" for name in ("significant", "balanced", "empty")
            }
            expected[str(experiments["conflict"].id)] = "Для расчёта требуются ровно два разных варианта"
            assert outcomes == repeated == expected, (
                f"Расчёт должен пропустить паузу и продолжить после конфликта: {outcomes}, {repeated}"
            )
            async with sessions() as db:
                results = list(await db.scalars(select(ExperimentResult)))
                reports = list(await db.scalars(select(ExperimentReport)))
            assert len(results) == 6, f"Ожидалось по два результата для трёх экспериментов: {len(results)}"
            assert {row.experiment_id for row in results if row.is_significant} == {experiments["significant"].id}, (
                "Значимость должна быть только у эксперимента с конверсиями 10/100 и 30/100"
            )
            assert len(reports) == 1 and reports[0].experiment_id == experiments["significant"].id, (
                f"Повторный расчёт создал лишние отчёты или отчёт для другого эксперимента: {len(reports)}"
            )
            assert reports[0].status == "completed" and reports[0].report_type == "template", (
                "Автоматический отчёт должен сохраняться сразу готовым шаблоном"
            )
            assert generate.await_count == 0, "Автоматический отчёт вызвал генерацию LLM"
            assert dispose.await_count == 2, "Задача не освободила движок после каждого запуска"
        finally:
            mongo.events.drop()


async def test_report_worker_completes_explicit_and_pending_jobs(
    experiment_db: tuple[UUID, UUID, UUID], task_database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сохранить отчёт по UUID и обработать оставшееся задание без снимка."""
    flag_id, owner_id, _ = experiment_db
    sessions, dispose = task_database
    control, treatment = VariantCounts(100, 10), VariantCounts(100, 30)
    context = ReportContext("Reports", "buy", "A", "B", control, treatment,
                            compare_conversions(control, treatment))
    generate = AsyncMock(return_value=GeneratedReport("Итог эксперимента", "llm", "test-model", 42))
    monkeypatch.setattr(report_service, "generate_report", generate)

    async with sessions.begin() as db:
        experiment = Experiment(name="Reports", primary_metric="buy", flag_id=flag_id, creator_id=owner_id)
        db.add(experiment)
        await db.flush()
        explicit = ExperimentReport(experiment_id=experiment.id, input_snapshot=asdict(context))
        pending = ExperimentReport(experiment_id=experiment.id)
        db.add_all([explicit, pending])

    await report_tasks._run_reports(explicit.id)
    await report_tasks._run_reports(explicit.id)
    async with sessions() as db:
        saved = await db.get(ExperimentReport, explicit.id)
        remaining = await db.get(ExperimentReport, pending.id)
        assert saved.status == "completed" and saved.report_type == "llm", (
            "Задание по UUID не сохранило готовый отчёт LLM"
        )
        assert (saved.report_content, saved.llm_model, saved.tokens_used) == ("Итог эксперимента", "test-model", 42), (
            "В PostgreSQL не сохранился текст отчёта, модель или число токенов"
        )
        assert remaining.status == "pending", "Запуск по UUID затронул другое задание"
    assert generate.await_count == 1 and generate.await_args.args[0].control == control, (
        "Повторная обработка повторно вызвала LLM или потеряла метрики снимка"
    )

    await report_tasks._run_reports()
    async with sessions() as db:
        failed = await db.get(ExperimentReport, pending.id)
        assert failed.status == "failed" and failed.fallback_reason == "missing_snapshot", (
            "Задание без снимка должно завершиться ошибкой с причиной missing_snapshot"
        )
    assert generate.await_count == 1, "Подбор pending обработал завершённый отчёт или вызвал LLM без снимка"
    assert dispose.await_count == 3, "Задача не освободила движок после каждого запуска"


async def test_pending_worker_selects_oldest_hundred_reports(
    experiment_db: tuple[UUID, UUID, UUID], task_database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Выбрать только pending, ограничить пачку сотней и упорядочить одинаковые даты по UUID."""
    flag_id, owner_id, _ = experiment_db
    sessions, _ = task_database
    timestamp = datetime.now(timezone.utc)
    async with sessions.begin() as db:
        experiment = Experiment(name="Queue", primary_metric="buy", flag_id=flag_id, creator_id=owner_id)
        db.add(experiment)
        await db.flush()
        db.add_all([
            ExperimentReport(
                id=UUID(int=index), experiment_id=experiment.id, status="pending",
                created_at=timestamp + timedelta(seconds=(index - 1) // 2),
            )
            for index in range(101, 0, -1)
        ])
        db.add(ExperimentReport(
            id=UUID(int=102), experiment_id=experiment.id, status="completed",
            created_at=timestamp - timedelta(seconds=1),
        ))
    complete = AsyncMock()
    monkeypatch.setattr(report_tasks, "complete_report", complete)

    await report_tasks._run_reports()

    selected = [call.args[1] for call in complete.await_args_list]
    assert selected == [UUID(int=index) for index in range(1, 101)], (
        f"Ожидались первые 100 pending в порядке даты и UUID, выбрано {selected}"
    )
