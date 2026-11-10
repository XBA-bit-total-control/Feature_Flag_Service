import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from src.database import get_async_session
from src.main import app
from src.models.experiment import Experiment, ExperimentStatus
from src.models.flag import Flag
from src.models.user import User, UserRole
from src.schemas.experiment import ExperimentCreate
from src.services import experiment_service, flag_service


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
        reason="Нужна явно включённая проверка локальной PostgreSQL",
    ),
]


async def test_admin_lists_experiments_across_owners(
    experiment_db: tuple[UUID, UUID, UUID],
) -> None:
    """Проверить, что администратор видит эксперименты разных владельцев и может фильтровать список."""
    flag_id, owner_id, other_id = experiment_db
    async for db in app.dependency_overrides[get_async_session]():
        owner = await db.get(User, owner_id)
        other = await db.get(User, other_id)
        foreign_flag = Flag(key="foreign-checkout", owner_id=other_id)
        db.add(foreign_flag)
        await db.flush()

        own = await experiment_service.create_experiment(db, ExperimentCreate(
            flag_id=flag_id, name="Owner experiment", primary_metric="buy",
        ), owner)
        foreign = await experiment_service.create_experiment(db, ExperimentCreate(
            flag_id=foreign_flag.id, name="Other experiment", primary_metric="buy",
        ), other)

        visible_to_owner = await experiment_service.list_experiments(
            db, owner, None, None, limit=10, offset=0,
        )
        assert [item.id for item in visible_to_owner] == [own.id], (
            "Разработчик должен видеть только эксперимент собственного флага"
        )

        other.role = UserRole.ADMIN
        visible_to_admin = await experiment_service.list_experiments(
            db, other, None, None, limit=10, offset=0,
        )
        assert {item.id for item in visible_to_admin} == {own.id, foreign.id}, (
            "Администратор должен видеть эксперименты обоих владельцев"
        )
        filtered = await experiment_service.list_experiments(
            db, other, ExperimentStatus.DRAFT, flag_id, limit=10, offset=0,
        )
        assert [item.id for item in filtered] == [own.id], (
            "Фильтры администратора должны оставить черновик только указанного флага"
        )


async def test_archived_flag_rejects_experiment_creation_and_start(
    experiment_db: tuple[UUID, UUID, UUID],
) -> None:
    """Сохранить черновик при архивации флага и запретить его запуск."""
    flag_id, owner_id, _ = experiment_db
    async for db in app.dependency_overrides[get_async_session]():
        owner = await db.get(User, owner_id)
        draft = await experiment_service.create_experiment(db, ExperimentCreate(
            flag_id=flag_id, name="Before archive", primary_metric="buy",
        ), owner)
        flag = await db.get(Flag, flag_id)
        await flag_service.delete_flag(db, flag)

        with pytest.raises(experiment_service.ExperimentNotFoundError, match="Флаг не найден"):
            await experiment_service.create_experiment(db, ExperimentCreate(
                flag_id=flag_id, name="After archive", primary_metric="buy",
            ), owner)
        with pytest.raises(experiment_service.ExperimentConflictError, match="удалённого флага"):
            await experiment_service.change_status(db, draft.id, owner, ExperimentStatus.RUNNING)

        saved = await experiment_service.get_experiment(db, draft.id, owner)
        assert saved.status == ExperimentStatus.DRAFT and saved.start_date is None, (
            "После отказа в запуске эксперимент должен остаться черновиком без даты старта"
        )


@pytest.mark.parametrize(("violation", "expected", "sqlstate", "constraint"), [
    ("active", experiment_service.ExperimentConflictError, "23505", "uq_experiments_active_flag"),
    ("creator", experiment_service.ExperimentNotFoundError, "23503", "experiments_creator_id_fkey"),
    ("alpha", IntegrityError, "23514", "ck_experiments_alpha"),
])
async def test_flush_experiment_maps_postgres_constraint_errors(
    experiment_db: tuple[UUID, UUID, UUID], violation: str,
    expected: type[Exception], sqlstate: str, constraint: str,
) -> None:
    """Проверить преобразование известных ошибок БД и проброс неизвестного ограничения."""
    flag_id, owner_id, _ = experiment_db
    async for db in app.dependency_overrides[get_async_session]():
        db.add(Experiment(
            name="Already running", primary_metric="buy", flag_id=flag_id,
            creator_id=owner_id, status=ExperimentStatus.RUNNING,
        ))
        await db.flush()

        invalid = Experiment(
            name="Invalid row", primary_metric="buy", flag_id=flag_id,
            creator_id=uuid4() if violation == "creator" else owner_id,
            status=ExperimentStatus.PAUSED if violation == "active" else ExperimentStatus.DRAFT,
            alpha=2.0 if violation == "alpha" else 0.05,
        )
        # Обходим проверки сервиса, чтобы получить реальную ошибку на flush.
        with pytest.raises(expected) as raised:
            async with db.begin_nested():
                db.add(invalid)
                await experiment_service._flush_experiment(db, invalid)

        error = raised.value if isinstance(raised.value, IntegrityError) else raised.value.__cause__
        assert isinstance(error, IntegrityError), "Сервис должен сохранить исходную ошибку PostgreSQL"
        assert error.orig.sqlstate == sqlstate, (
            f"Ожидался SQLSTATE {sqlstate}, получен {error.orig.sqlstate}"
        )
        assert getattr(error.orig.__cause__, "constraint_name", None) == constraint, (
            f"Сработало другое ограничение вместо {constraint}: {error.orig}"
        )
        if violation == "active":
            assert "запущенный или приостановленный эксперимент" in str(raised.value), (
                "Конфликт активного эксперимента ошибочно сообщён как конфликт имени"
            )
