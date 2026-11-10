import json
import os
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from kombu.exceptions import OperationalError
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from pymongo import MongoClient
from pymongo.collection import Collection
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.database import DATABASE_URL, get_async_session, get_mongo_database
from src.main import app
from src.models import Base, Flag, FlagVariant, User
from src.models.audit_log import AuditLog
from src.models.experiment import Experiment, ExperimentStatus
from src.models.experiment_participant import ExperimentParticipant
from src.models.user import UserRole
from src.utils.jwt_handler import create_access_token
from src.celery_app import app as celery_app
from src.services import event_service
from src.services import report_service
from src.services.event_retention import _clean_experiment, cleanup_old_events
from src.services.experiment_results_service import calculate_results
from src.services.experiment_reports_service import complete_report, save_automatic_report
from src.tasks import event_tasks
from src.api import resolve as resolve_api
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка локальной PostgreSQL",
)


@pytest.mark.skipif(
    os.environ.get("RUN_MONGO_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка MongoDB",
)
@pytest.mark.asyncio
async def test_resolve_impression_to_statistics(
    experiment_db: tuple[UUID, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повторные показы и конверсии дают одного участника в статистике."""
    flag_id, owner_id, _ = experiment_db
    headers = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    publish = Mock(side_effect=OperationalError("broker unavailable"))
    monkeypatch.setattr(event_service.celery_app, "send_task", publish)
    with MongoClient(
        host=settings.MONGO_HOST, port=settings.MONGO_PORT, serverSelectionTimeoutMS=3000
    ) as mongo:
        database = mongo[f"codex_impressions_{uuid4().hex}"]
        monkeypatch.setitem(app.dependency_overrides, get_mongo_database, lambda: database)
        monkeypatch.setattr(event_tasks, "get_mongo_database", lambda: database)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                payload = {"flag_key": "checkout", "user_id": "u1"}
                disabled = await client.post("/resolve", json=payload)
                assert disabled.status_code == 200, f"Ошибка resolve: {disabled.text}"
                assert database.events.count_documents({}) == 0, (
                    "Выключенный флаг создал impression"
                )
                for key in ("A", "B"):
                    variant = await client.post(
                        "/api/flags/checkout/variants", headers=headers,
                        json={"flag_id": str(flag_id), "variant_key": key},
                    )
                    assert variant.status_code == 201, f"Не создан вариант: {variant.text}"
                experiment = await client.post("/api/experiments", headers=headers, json={
                    "flag_id": str(flag_id), "name": "Impressions", "primary_metric": "buy",
                })
                assert experiment.status_code == 201, f"Ошибка создания: {experiment.text}"
                path = f"/api/experiments/{experiment.json()['id']}"
                await client.patch(path, headers=headers, json={"status": "running"})
                await client.patch(
                    "/api/flags/checkout", headers=headers, json={"enabled": True}
                )
                first = await client.post("/resolve", json=payload)
                second = await client.post("/resolve", json=payload)
                assert first.status_code == second.status_code == 200, (
                    f"Сбой брокера повлиял на назначение: {first.text}, {second.text}"
                )
                result = first.json()
                shows = list(database.events.find({"event_type": "impression"}))
                assert len(shows) == 2 and all(
                    show["variant_key"] == result["variant"] for show in shows
                ), "Не сохранены два показа назначенного варианта"
                conversion = {
                    **payload, "experiment_id": result["experiment_id"],
                    "variant_key": result["variant"], "event_type": "conversion",
                    "event_name": "buy",
                }
                batch = await client.post(
                    "/api/events", json={"events": [conversion, conversion]}
                )
                assert batch.status_code == 201, f"Конверсии не приняты: {batch.text}"
                assert event_tasks.process_pending_events.run() == 4, (
                    "Воркер должен обработать два показа и две конверсии"
                )
                stats = await client.post(path + "/results", headers=headers)
                assert stats.status_code == 200, f"Расчёт не выполнен: {stats.text}"
                rows = stats.json()
                assert sum(row["sample_size"] for row in rows) == 1, (
                    f"Повторные показы увеличили число участников: {rows}"
                )
                assert sum(row["conversions"] for row in rows) == 1, (
                    f"Повторные конверсии увеличили число успехов: {rows}"
                )
                monkeypatch.setattr(
                    resolve_api, "save_event",
                    Mock(side_effect=ServerSelectionTimeoutError("internal host")),
                )
                failed = await client.post("/resolve", json=payload)
                assert failed.status_code == 503 and "internal host" not in failed.text, (
                    f"Некорректная обработка отказа MongoDB: {failed.text}"
                )
        finally:
            database.events.drop()


@pytest.mark.asyncio
async def test_resolve_api_with_experiment(
    experiment_db: tuple[UUID, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve проверяет доступность флага и связь с запущенным экспериментом."""
    flag_id, owner_id, _ = experiment_db
    save_event = Mock()
    monkeypatch.setattr(resolve_api, "save_event", save_event)
    monkeypatch.setattr(resolve_api, "schedule_processing", Mock())
    headers = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        async def resolve(key="checkout", **kwargs):
            return await client.post(
                "/resolve", json={"flag_key": key, "user_identifier": "u1", **kwargs}
            )

        disabled = await resolve()
        assert disabled.status_code == 200 and not disabled.json()["enabled"], (
            f"Выключенный флаг должен быть недоступен: {disabled.text}"
        )
        await client.patch("/api/flags/checkout", headers=headers, json={"enabled": True})
        inactive = await resolve()
        assert inactive.json()["reason"] == "no_active_experiment", (
            f"Флаг без запущенного эксперимента не должен назначать вариант: {inactive.text}"
        )
        for key in ("A", "B"):
            variant = await client.post(
                "/api/flags/checkout/variants", headers=headers,
                json={"flag_id": str(flag_id), "variant_key": key},
            )
            assert variant.status_code == 201, f"Вариант не создан: {variant.text}"
        created = await client.post("/api/experiments", headers=headers, json={
            "flag_id": str(flag_id), "name": "Resolve", "primary_metric": "click",
        })
        assert created.status_code == 201, f"Эксперимент не создан: {created.text}"
        experiment_id = created.json()["id"]
        path = f"/api/experiments/{experiment_id}"
        started = await client.patch(path, headers=headers, json={"status": "running"})
        assert started.status_code == 200, f"Эксперимент не запущен: {started.text}"
        rule = await client.post("/api/flags/checkout/targeting", headers=headers, json={
            "attribute": "country", "operator": "eq", "values": ["RU"],
        })
        assert rule.status_code == 201, f"Правило A/B-флага не создано: {rule.text}"
        blocked = await resolve(attributes={"country": "KZ"})
        assert blocked.status_code == 200 and blocked.json()["experiment_id"] is None, (
            f"Таргетинг не остановил A/B-назначение: {blocked.text}"
        )
        assert blocked.json()["reason"] == "targeting" and save_event.call_count == 0, (
            f"Отклонённый запрос записал показ или вернул неверную причину: {blocked.text}"
        )
        first = await resolve(attributes={"country": "RU"})
        second = await resolve(attributes={"country": "RU"})
        assert first.status_code == 200 and first.json() == second.json(), (
            f"Повтор запроса изменил назначение: {first.text}, {second.text}"
        )
        result = first.json()
        assert result["enabled"] and result["experiment_id"] == experiment_id, (
            f"Не возвращён запущенный эксперимент: {result}"
        )
        assert result["variant"] == result["variant_key"] in ("A", "B"), (
            f"Старое и новое поля варианта не согласованы: {result}"
        )
        await client.patch(path, headers=headers, json={"status": "paused"})
        paused = await resolve()
        assert not paused.json()["enabled"], "Приостановленный эксперимент назначил вариант"
        missing = await resolve("missing")
        assert missing.status_code == 404, f"Неизвестный флаг дал {missing.status_code}"


@pytest_asyncio.fixture
async def experiment_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[UUID, UUID, UUID]]:
    """Подготовить изолированную схему PostgreSQL и тестовых владельцев."""
    engine = create_async_engine(DATABASE_URL)
    schema = f"codex_experiments_{uuid4().hex}"
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
                await connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
                await connection.run_sync(Base.metadata.create_all)

                async def test_session() -> AsyncIterator[AsyncSession]:
                    async with AsyncSession(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    ) as session:
                        try:
                            yield session
                            await session.commit()
                        except Exception:
                            await session.rollback()
                            raise

                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    owner = User(email="owner@example.test", hashed_password="unused", role=UserRole.DEVELOPER)
                    other = User(email="other@example.test", hashed_password="unused", role=UserRole.DEVELOPER)
                    session.add_all([owner, other])
                    await session.flush()
                    flag = Flag(key="checkout", type="ab", owner_id=owner.id)
                    session.add(flag)
                    await session.commit()

                monkeypatch.setitem(
                    app.dependency_overrides, get_async_session, test_session
                )
                yield flag.id, owner.id, other.id
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("RUN_MONGO_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка MongoDB",
)
async def test_event_retention_preserves_statistics(
    experiment_db: tuple[UUID, UUID, UUID], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверить пороги удаления, повтор после сбоя и неизменность пересчёта."""
    flag_id, owner_id, _ = experiment_db
    now = datetime.now(timezone.utc).replace(microsecond=0)
    async for db in app.dependency_overrides[get_async_session]():
        sessions = async_sessionmaker(
            db.bind, join_transaction_mode="create_savepoint", expire_on_commit=False,
        )
        active = Experiment(name="Active retention", flag_id=flag_id, creator_id=owner_id,
                            primary_metric="buy", status=ExperimentStatus.RUNNING)
        terminal = Experiment(name="Terminal retention", flag_id=flag_id, creator_id=owner_id,
                              primary_metric="buy", status=ExperimentStatus.STOPPED)
        db.add_all([active, terminal, FlagVariant(flag_id=flag_id, variant_key="A"),
                    FlagVariant(flag_id=flag_id, variant_key="B")])
        await db.flush()

    with MongoClient(host=settings.MONGO_HOST, port=settings.MONGO_PORT,
                     serverSelectionTimeoutMS=3000) as client:
        mongo = client[f"codex_retention_{uuid4().hex}"]
        try:
            def event(experiment, user_id, age, *, processed=True, **extra):
                return {
                    "experiment_id": str(experiment.id), "flag_key": "checkout",
                    "variant_key": "A", "user_id": user_id, "event_type": "impression",
                    "timestamp": now - timedelta(days=age), "processed": processed, **extra,
                }

            old_impression = event(active, "u1", 40)
            old_conversion = event(active, "u2", 35, event_type="conversion", event_name="buy")
            mongo.events.insert_many([
                old_impression, event(active, "u1", 1, event_type="conversion", event_name="buy"),
                old_conversion, event(active, "u2", 45),
                event(active, "pending", 40, processed=False), event(active, "boundary", 30),
                event(terminal, "expired", 91, processed=False), event(terminal, "keep", 90),
            ])

            async def counts():
                async with sessions.begin() as db:
                    rows = await calculate_results(db, mongo, active.id)
                    return [(row.sample_size, row.conversions) for row in rows]

            before = await counts()
            assert before == [(4, 2), (0, 0)], f"Неверные исходные счётчики: {before}"
            with monkeypatch.context() as scoped:
                scoped.setattr(Collection, "delete_many", Mock(side_effect=ConnectionFailure("test")))
                with pytest.raises(ConnectionFailure):
                    await _clean_experiment(sessions, mongo.events, active.id, now)
            async with sessions() as db:
                archived = await db.scalars(select(ExperimentParticipant).where(
                    ExperimentParticipant.experiment_id == active.id,
                ))
                assert len(archived.all()) == 2, "Состояние не зафиксировано до сбоя MongoDB"
            assert await counts() == before, "Исходные и компактные данные задвоили статистику"

            result = await cleanup_old_events(sessions, mongo, now)
            assert result == {"active_removed": 3, "terminal_removed": 1, "invalid_experiments": 0}, (
                f"Удалены неверные события: {result}"
            )
            kept = set(mongo.events.distinct("user_id"))
            assert kept == {"u1", "pending", "boundary", "keep"}, f"Неверный остаток: {kept}"
            assert await counts() == before, "После очистки изменились счётчики эксперимента"
            repeated = await cleanup_old_events(sessions, mongo, now)
            assert repeated["active_removed"] == repeated["terminal_removed"] == 0, (
                f"Повторное выполнение снова удалило события: {repeated}"
            )
        finally:
            mongo.events.drop()


@pytest.mark.asyncio
async def test_roles_audit_and_soft_delete(experiment_db: tuple[UUID, UUID, UUID]) -> None:
    """Проверить границы ролей и сохранение истории после удаления флага."""
    flag_id, owner_id, admin_id = experiment_db
    sessions = app.dependency_overrides[get_async_session]
    async for db in sessions():
        admin = await db.get(User, admin_id)
        admin.role = UserRole.ADMIN

    admin_headers = {"Authorization": f"Bearer {create_access_token(str(admin_id))}"}
    owner_headers = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=admin_headers,
    ) as client:
        registered = await client.post("/auth/register", json={
            "email": "reader@example.com", "password": "integration-test-password",
        })
        assert registered.status_code == 201 and registered.json()["role"] == "viewer", (
            f"Регистрация должна выдать роль viewer: {registered.text}"
        )
        viewer_id = registered.json()["id"]
        viewer_headers = {"Authorization": f"Bearer {create_access_token(viewer_id)}"}
        role_path = f"/auth/users/{viewer_id}/role"
        denied = await client.post("/api/flags", headers=viewer_headers, json={"key": "forbidden"})
        escalation = await client.patch(role_path, headers=viewer_headers, json={"role": "admin"})
        assert denied.status_code == escalation.status_code == 403, (
            f"Viewer получил право записи или смены роли: {denied.text}, {escalation.text}"
        )
        promoted = await client.patch(role_path, json={"role": "developer"})
        profile = await client.get("/auth/me", headers=viewer_headers)
        assert promoted.status_code == 200 and profile.json()["role"] == "developer", (
            f"Роль не обновилась для существующего токена: {promoted.text}, {profile.text}"
        )
        changed = await client.patch("/api/flags/checkout", json={"enabled": True})
        assert changed.status_code == 200, f"Администратор не может изменить чужой флаг: {changed.text}"
        variant = await client.post("/api/flags/checkout/variants", json={
            "flag_id": str(flag_id), "variant_key": "A",
        })
        assert variant.status_code == 201, f"Не создан вариант: {variant.text}"
        variant_path = f"/api/flags/variants/{variant.json()['id']}"
        created = await client.post("/api/experiments", json={
            "name": "Archive test", "flag_id": str(flag_id), "primary_metric": "buy",
        })
        assert created.status_code == 201, f"Эксперимент не создан: {created.text}"
        path = f"/api/experiments/{created.json()['id']}"
        started = await client.patch(path, headers=owner_headers, json={"status": "running"})
        assert started.status_code == 200, f"Владелец флага не смог запустить эксперимент: {started.text}"
        denied_variant_delete = await client.delete(variant_path)
        assert denied_variant_delete.status_code == 409, (
            f"Удалён вариант активного эксперимента: {denied_variant_delete.text}"
        )
        denied_delete = await client.delete("/api/flags/checkout")
        assert denied_delete.status_code == 409, f"Активный флаг был удалён: {denied_delete.text}"
        stopped = await client.patch(path, json={"status": "stopped"})
        assert stopped.status_code == 200, f"Эксперимент не остановлен: {stopped.text}"
        deleted = await client.delete("/api/flags/checkout")
        assert deleted.status_code == 204, f"Флаг не архивирован: {deleted.text}"
        hidden = await client.post("/resolve", json={"flag_key": "checkout", "user_id": "test"})
        history = await client.get(path, headers=owner_headers)
        assert hidden.status_code == 404 and history.status_code == 200, (
            f"Флаг остался доступен или история потеряна: {hidden.text}, {history.text}"
        )
        archived_variant = await client.get(variant_path)
        assert archived_variant.status_code == 404, f"Вариант архивного флага доступен: {archived_variant.text}"
        recreated = await client.post("/api/flags", json={"key": "checkout"})
        assert recreated.status_code == 409, f"Ключ архивного флага был переиспользован: {recreated.text}"

    async for db in sessions():
        deletes = list(await db.scalars(select(AuditLog).where(AuditLog.action == "flag.deleted")))
        assert len(deletes) == 1 and deletes[0].user_id == admin_id, (
            "Аудит должен содержать только успешное удаление и ID администратора"
        )
        assert json.loads(deletes[0].old_value)["deleted_at"] is None, "Потеряно состояние до удаления"
        assert json.loads(deletes[0].new_value)["deleted_at"], "Не записана дата мягкого удаления"
        role_change = await db.scalar(select(AuditLog).where(AuditLog.action == "user.role_changed"))
        assert role_change is not None and json.loads(role_change.new_value) == {"role": "developer"}, (
            "В журнале нет новой роли или в запись попали посторонние данные"
        )


@pytest.mark.asyncio
async def test_flag_pages_and_variant_uniqueness(experiment_db: tuple[UUID, UUID, UUID]) -> None:
    """Проверить фильтрацию, порядок страниц и защиту от дублей на уровне БД."""
    flag_id, owner_id, _ = experiment_db
    headers = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers,
    ) as client:
        created = await client.post("/api/flags", json={"key": "alpha", "enabled": True})
        assert created.status_code == 201, f"Не создан второй флаг: {created.text}"
        first = await client.get("/api/flags?limit=1")
        second = await client.get("/api/flags?limit=1&offset=1")
        assert [row["key"] for row in first.json()] == ["alpha"], f"Неверная первая страница: {first.text}"
        assert [row["key"] for row in second.json()] == ["checkout"], f"Неверная вторая страница: {second.text}"
        filtered = await client.get("/api/flags?type=simple&enabled=true")
        assert [row["key"] for row in filtered.json()] == ["alpha"], f"Фильтры не применены: {filtered.text}"
        invalid = await client.get("/api/experiments?limit=0")
        assert invalid.status_code == 422, f"Нулевой размер страницы принят: {invalid.text}"

        variant = {"flag_id": str(flag_id), "variant_key": "A"}
        first_variant = await client.post("/api/flags/checkout/variants", json=variant)
        duplicate = await client.post("/api/flags/checkout/variants", json=variant)
        other_variant = await client.post("/api/flags/alpha/variants", json=variant)
        assert first_variant.status_code == other_variant.status_code == 201, (
            f"Один ключ должен быть допустим на двух флагах: {first_variant.text}, {other_variant.text}"
        )
        assert duplicate.status_code == 409, f"API принял дубль на одном флаге: {duplicate.text}"

    async for db in app.dependency_overrides[get_async_session]():
        with pytest.raises(IntegrityError) as error:
            async with db.begin_nested():
                db.add(FlagVariant(flag_id=flag_id, variant_key="A"))
                await db.flush()
        assert getattr(error.value.orig.__cause__, "constraint_name", None) == "uq_flag_variants_flag_key", (
            f"Ожидалось нарушение уникальности варианта: {error.value}"
        )


@pytest.mark.asyncio
async def test_targeting_api_and_resolve(experiment_db: tuple[UUID, UUID, UUID]) -> None:
    """Проверить CRUD владельца и фильтрацию до rollout."""
    _, owner_id, other_id = experiment_db
    owner = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    other = {"Authorization": f"Bearer {create_access_token(str(other_id))}"}
    path = "/api/flags/variants/targeting"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers=owner,
    ) as client:
        flag = await client.post("/api/flags", json={"key": "variants", "enabled": True})
        assert flag.status_code == 201, f"Не создан флаг: {flag.text}"
        foreign = await client.get(path, headers=other)
        assert foreign.status_code == 403, f"Чужой пользователь прочитал правила: {foreign.text}"
        country = {"attribute": "country", "operator": "in", "values": ["RU", "KZ"]}
        age = {"attribute": "age", "operator": "gte", "values": [18]}
        created = await client.post(path, json=country)
        second = await client.post(path, json=age)
        assert created.status_code == second.status_code == 201, (
            f"Правила не созданы: {created.text}, {second.text}"
        )
        rule_path = f"{path}/{created.json()['id']}"
        wrong_flag = await client.delete(f"/api/flags/checkout/targeting/{created.json()['id']}")
        assert wrong_flag.status_code == 404, f"Удалено правило другого флага: {wrong_flag.text}"

        async def resolve(attributes):
            return await client.post("/resolve", json={
                "flag_key": "variants", "user_id": "u1", "attributes": attributes,
            })

        allowed = await resolve({"country": "RU", "age": 18})
        blocked = await resolve({"country": "RU"})
        assert allowed.status_code == 200 and allowed.json()["enabled"], (
            f"Совпадение с обоими правилами отклонено: {allowed.text}"
        )
        assert blocked.status_code == 200 and not blocked.json()["enabled"], (
            f"Пользователь без возраста прошёл таргетинг: {blocked.text}"
        )
        assert blocked.json()["reason"] == "targeting", f"Неверная причина отказа: {blocked.text}"
        replaced = await client.put(rule_path, json={**country, "is_enabled": False})
        assert replaced.status_code == 200 and not replaced.json()["is_enabled"], (
            f"Правило не отключено: {replaced.text}"
        )
        allowed_without_country = await resolve({"age": 18})
        assert allowed_without_country.json()["enabled"], (
            f"Отключённое правило продолжает блокировать флаг: {allowed_without_country.text}"
        )
        deleted = await client.delete(rule_path)
        assert deleted.status_code == 204, f"Правило не удалено: {deleted.text}"
        listed = await client.get(path)
        assert listed.status_code == 200 and listed.json() == [second.json()], (
            f"После удаления ожидалось только правило возраста: {listed.text}"
        )


@pytest.mark.asyncio
async def test_experiment_lifecycle(experiment_db: tuple[UUID, UUID, UUID]) -> None:
    """API проверяет владельца, уникальность имени и переходы статусов."""
    flag_id, owner_id, other_id = experiment_db
    transport = httpx.ASGITransport(app=app)
    owner = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    other = {"Authorization": f"Bearer {create_access_token(str(other_id))}"}
    base = "/api/experiments"
    payload = {
        "flag_id": str(flag_id), "name": "Checkout", "primary_metric": "conversion"
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async def request(method, path, expected, headers=owner, **kwargs):
            response = await client.request(method, path, headers=headers, **kwargs)
            assert response.status_code == expected, (
                f"{method} {path}: ожидался статус {expected}, "
                f"получено {response.status_code}, ответ {response.text}"
            )
            return response

        await request("GET", base, 401, headers={})
        await request("POST", base, 403, headers=other, json=payload)
        await request("POST", base, 404, json={**payload, "flag_id": str(uuid4())})
        created = (await request("POST", base, 201, json=payload)).json()
        assert created["status"] == "draft" and created["start_date"] is None, (
            f"Новый эксперимент должен быть черновиком без даты старта: {created}"
        )
        await request("POST", base, 409, json=payload)
        path = f"{base}/{created['id']}"
        await request("GET", path, 200)
        await request("GET", path, 403, headers=other)
        await request("PATCH", path, 403, headers=other, json={"status": "running"})
        await request("PATCH", path, 422, json={})
        await request("PATCH", path, 422, json={"min_sample_size": 2**31})
        await request("PATCH", path, 422, json={"status": "running", "name": "New"})
        updated = (await request("PATCH", path, 200, json={"min_sample_size": 100})).json()
        assert updated["min_sample_size"] == 100, (
            f"Ожидалась выборка 100, получено {updated['min_sample_size']}"
        )

        another = (await request("POST", base, 201, json={**payload, "name": "Next"})).json()
        another_path = f"{base}/{another['id']}"
        await request("PATCH", another_path, 409, json={"name": "Checkout"})
        await request("PATCH", path, 409, json={"status": "completed"})
        started = (await request("PATCH", path, 200, json={"status": "running"})).json()
        assert started["start_date"] is not None, "При запуске ожидается дата старта"
        await request("PATCH", path, 409, json={"alpha": 0.1})
        await request("DELETE", path, 409)
        await request("PATCH", path, 200, json={"status": "paused"})
        await request("PATCH", another_path, 409, json={"status": "running"})
        resumed = (await request("PATCH", path, 200, json={"status": "running"})).json()
        assert resumed["start_date"] == started["start_date"], (
            "Возобновление должно сохранять первоначальную дату старта"
        )

        listed = (await request(
            "GET", base, 200, params={"flag_id": str(flag_id), "status": "running"}
        )).json()
        assert [item["id"] for item in listed] == [created["id"]], (
            f"Фильтры должны вернуть только запущенный эксперимент: {listed}"
        )
        foreign_list = (await request("GET", base, 200, headers=other)).json()
        assert foreign_list == [], f"Список раскрыл чужие эксперименты: {foreign_list}"
        completed = (
            await request("PATCH", path, 200, json={"status": "completed"})
        ).json()
        repeated = (
            await request("PATCH", path, 200, json={"status": "completed"})
        ).json()
        assert completed["end_date"] and repeated["end_date"] == completed["end_date"], (
            "Повтор завершения должен сохранять дату окончания"
        )
        await request("PATCH", path, 409, json={"status": "running"})
        await request("DELETE", another_path, 204)
        await request("GET", another_path, 404)


@pytest.mark.skipif(
    os.environ.get("RUN_MONGO_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка локальной MongoDB",
)
@pytest.mark.asyncio
async def test_events_require_valid_experiment_links(
    experiment_db: tuple[UUID, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Некорректные связи отклоняются до записи и публикации задачи."""
    flag_id, owner_id, _ = experiment_db
    headers = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    publish = Mock()
    monkeypatch.setattr(event_service.celery_app, "send_task", publish)

    with MongoClient(
        host=settings.MONGO_HOST, port=settings.MONGO_PORT, serverSelectionTimeoutMS=3000
    ) as mongo:
        database = mongo[f"codex_event_links_{uuid4().hex}"]
        collection = database["events"]
        monkeypatch.setitem(app.dependency_overrides, get_mongo_database, lambda: database)
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                variant = await client.post(
                    "/api/flags/checkout/variants", headers=headers,
                    json={"flag_id": str(flag_id), "variant_key": "A", "weight": 1},
                )
                assert variant.status_code == 201, f"Вариант не создан: {variant.text}"
                other_flag = await client.post(
                    "/api/flags", headers=headers, json={"key": "other", "type": "ab"},
                )
                assert other_flag.status_code == 201, (
                    f"Второй флаг не создан: {other_flag.text}"
                )
                foreign_variant = await client.post(
                    "/api/flags/other/variants", headers=headers,
                    json={"flag_id": other_flag.json()["id"], "variant_key": "B", "weight": 1},
                )
                assert foreign_variant.status_code == 201, (
                    f"Вариант второго флага не создан: {foreign_variant.text}"
                )
                experiment = await client.post(
                    "/api/experiments", headers=headers,
                    json={"flag_id": str(flag_id), "name": "Checkout", "primary_metric": "click"},
                )
                assert experiment.status_code == 201, f"Эксперимент не создан: {experiment.text}"
                experiment_id = experiment.json()["id"]
                path = f"/api/experiments/{experiment_id}"
                event = {
                    "experiment_id": experiment_id, "flag_key": "checkout",
                    "variant_key": "A", "event_type": "click", "user_id": "user-1",
                }
                draft = await client.post("/api/events/single", json=event)
                assert draft.status_code == 422, f"Событие черновика принято: {draft.text}"
                running = await client.patch(path, headers=headers, json={"status": "running"})
                assert running.status_code == 200, f"Эксперимент не запущен: {running.text}"

                for changes in (
                    {"experiment_id": str(uuid4())}, {"flag_key": "wrong"},
                    {"variant_key": "B"}, {"variant_key": None},
                ):
                    response = await client.post("/api/events/single", json={**event, **changes})
                    assert response.status_code == 422, (
                        f"Неверная связь {changes} не отклонена: {response.text}"
                    )
                mixed = await client.post(
                    "/api/events", json={"events": [event, {**event, "variant_key": "B"}]},
                )
                assert mixed.status_code == 422 and "Событие 2" in mixed.json()["detail"], (
                    f"Пакет должен указать второе событие: {mixed.text}"
                )
                count = collection.count_documents({})
                assert count == 0, f"Невалидные запросы сохранили {count} документов"
                assert publish.call_count == 0, "Невалидные запросы отправили задачу"

                single = await client.post("/api/events/single", json=event)
                batch = await client.post("/api/events", json={"events": [event, event]})
                assert single.status_code == 201, f"Одиночное событие отклонено: {single.text}"
                assert batch.status_code == 201, f"Корректный пакет отклонён: {batch.text}"
                paused = await client.patch(path, headers=headers, json={"status": "paused"})
                assert paused.status_code == 200, f"Не удалось приостановить: {paused.text}"
                rejected = await client.post("/api/events/single", json=event)
                assert rejected.status_code == 422, (
                    f"Приостановленный эксперимент принял событие: {rejected.text}"
                )
                count = collection.count_documents({"experiment_id": experiment_id})
                assert count == 3, f"Ожидалось три связанных события, получено {count}"
                assert publish.call_count == 2, (
                    f"Ожидались две задачи, отправлено {publish.call_count}"
                )
        finally:
            collection.drop()


@pytest.mark.skipif(
    os.environ.get("RUN_MONGO_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка MongoDB",
)
@pytest.mark.asyncio
async def test_statistics_results_api(
    experiment_db: tuple[UUID, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Результаты сохраняются без дубликатов и доступны только владельцу."""
    flag_id, owner_id, other_id = experiment_db
    publish = Mock(side_effect=OperationalError("broker unavailable"))
    monkeypatch.setattr(celery_app, "send_task", publish)

    # Подмена точки вызова оставляет реализацию шаблонного клиента доступной.
    original_generate = report_service.generate_report

    async def offline_report(context):
        return await original_generate(
            context, settings.model_copy(update={"LLM_API_KEY": None})
        )

    generator = AsyncMock(side_effect=offline_report)
    monkeypatch.setattr(report_service, "generate_report", generator)
    headers = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    foreign = {"Authorization": f"Bearer {create_access_token(str(other_id))}"}
    with MongoClient(
        host=settings.MONGO_HOST, port=settings.MONGO_PORT, serverSelectionTimeoutMS=3000
    ) as mongo:
        database = mongo[f"codex_statistics_{uuid4().hex}"]
        monkeypatch.setitem(app.dependency_overrides, get_mongo_database, lambda: database)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
                headers=headers,
            ) as client:
                for key in ("A", "B"):
                    response = await client.post(
                        "/api/flags/checkout/variants",
                        json={"flag_id": str(flag_id), "variant_key": key},
                    )
                    assert response.status_code == 201, f"Не создан вариант: {response.text}"
                response = await client.post("/api/experiments", json={
                    "flag_id": str(flag_id), "name": "Statistics", "primary_metric": "buy",
                })
                assert response.status_code == 201, f"Не создан эксперимент: {response.text}"
                experiment_id = response.json()["id"]
                path = f"/api/experiments/{experiment_id}"
                no_template = await client.get(path + "/template/reports")
                no_llm = await client.post(path + "/llm/reports")
                assert no_template.status_code == no_llm.status_code == 409, (
                    f"Без результатов оба маршрута должны вернуть 409: {no_template.text}, {no_llm.text}"
                )
                response = await client.patch(path, json={"status": "running"})
                assert response.status_code == 200, f"Не запущен эксперимент: {response.text}"
                empty = await client.post(path + "/results")
                assert empty.status_code == 200, f"Ошибка пустого расчёта: {empty.text}"
                assert all(not row["enough_data"] for row in empty.json()), (
                    "Пустая выборка ошибочно отмечена как достаточная"
                )
                from datetime import datetime, timedelta, timezone
                start = datetime.now(timezone.utc)
                for key, successes in (("A", 10), ("B", 30)):
                    for i in range(100):
                        data = {
                            "experiment_id": experiment_id, "flag_key": "checkout",
                            "variant_key": key, "user_id": f"{key}-{i}",
                            "event_type": "impression", "timestamp": start,
                        }
                        database.events.insert_one(dict(data))
                        if i < successes:
                            database.events.insert_one({
                                **data, "event_type": "conversion", "event_name": "buy",
                                "timestamp": start + timedelta(seconds=1),
                            })
                for _ in range(2):
                    result = await client.post(path + "/results")
                    assert result.status_code == 200, f"Расчёт не выполнен: {result.text}"
                    rows = result.json()
                    assert [r["conversions"] for r in rows] == [10, 30], (
                        f"Ожидались 10 и 30 конверсий, получено {rows}"
                    )
                saved = await client.get(path + "/results")
                assert saved.status_code == 200 and len(saved.json()) == 2, (
                    f"Ожидался один снимок из двух строк: {saved.text}"
                )
                denied = await client.get(path + "/results", headers=foreign)
                assert denied.status_code == 403, f"Чужие результаты доступны: {denied.text}"
                state = await client.get(path)
                assert state.json()["status"] == "running", (
                    "Статистика самопроизвольно завершила эксперимент"
                )
                assert celery_app.conf.beat_schedule["calculate-statistics"]["schedule"] == 1800, (
                    "Статистика должна запускаться каждые 30 минут"
                )
                denied_template = await client.get(path + "/template/reports", headers=foreign)
                denied_llm = await client.post(path + "/llm/reports", headers=foreign)
                assert denied_template.status_code == denied_llm.status_code == 403, (
                    f"Чужому владельцу доступны отчёты: {denied_template.text}, {denied_llm.text}"
                )
                template = await client.get(path + "/template/reports")
                assert template.status_code == 200 and template.json()["report_type"] == "template", (
                    f"Ожидался немедленный шаблонный ответ: {template.text}"
                )
                history = await client.get(path + "/reports")
                assert history.status_code == 200 and history.json() == [], (
                    f"Чтение шаблона не должно писать в историю: {history.text}"
                )
                assert publish.call_count == generator.await_count == 0, (
                    "Чтение шаблона вызвало брокер или LLM"
                )
                async for db in app.dependency_overrides[get_async_session]():
                    await save_automatic_report(db, UUID(experiment_id))
                    await save_automatic_report(db, UUID(experiment_id))
                assert generator.await_count == 0, "Автоматический отчёт обратился к LLM"

                queued = await client.post(path + "/llm/reports")
                assert queued.status_code == 202 and queued.json()["status"] == "pending", (
                    f"Задание должно сохраниться при отказе брокера: {queued.text}"
                )
                repeated = await client.post(path + "/llm/reports")
                assert repeated.status_code == 202 and repeated.json()["id"] == queued.json()["id"], (
                    f"Повтор должен вернуть то же ожидающее задание: {repeated.text}"
                )
                assert publish.call_count == 1, "Повторное задание было отправлено в очередь"
                report_id = UUID(queued.json()["id"])
                async for db in app.dependency_overrides[get_async_session]():
                    await complete_report(db, report_id)
                    await complete_report(db, report_id)
                reports = await client.get(path + "/reports")
                assert reports.status_code == 200 and len(reports.json()) == 2, (
                    f"Ожидались автоматический шаблон и ручной отчёт: {reports.text}"
                )
                ready = next(row for row in reports.json() if row["id"] == str(report_id))
                automatic = next(row for row in reports.json() if row["id"] != str(report_id))
                assert ready["status"] == "completed" and ready["fallback_reason"] == "missing_key", (
                    f"Без ключа ожидался готовый шаблон с причиной подмены: {ready}"
                )
                assert automatic["status"] == "completed" and automatic["fallback_reason"] is None, (
                    f"Автоматический шаблон не должен считаться подменой: {automatic}"
                )
                assert ready["report_type"] == automatic["report_type"] == "template", (
                    f"Оба сохранённых отчёта должны быть шаблонными: {reports.text}"
                )
                assert ready["report_content"] == automatic["report_content"] == template.json()["report_content"], (
                    "Три шаблона для одного снимка содержат разный текст"
                )
                assert generator.await_count == 1, "Ручной отчёт был обработан больше одного раза"
        finally:
            database.events.drop()
