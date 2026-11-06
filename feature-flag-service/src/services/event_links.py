from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.experiment import Experiment, ExperimentStatus
from src.models.flag import Flag
from src.models.flag_variant import FlagVariant
from src.schemas.event import EventCreate


class EventLinkError(ValueError):
    """Событие ссылается на неподходящий эксперимент, флаг или вариант."""


async def validate_event_links(db: AsyncSession, events: list[EventCreate]) -> None:
    """Проверить связи всех событий, которые ссылаются на эксперимент."""
    linked = [
        (index, event) for index, event in enumerate(events)
        if event.experiment_id is not None
    ]
    if not linked:
        return

    for index, event in linked:
        if not event.variant_key:
            raise EventLinkError(f"Событие {index + 1}: укажите вариант эксперимента")

    result = await db.execute(
        select(Experiment.id, Experiment.status, Flag.key, FlagVariant.variant_key)
        .join(Flag, Flag.id == Experiment.flag_id)
        .outerjoin(
            FlagVariant,
            and_(
                FlagVariant.flag_id == Flag.id,
                FlagVariant.variant_key.in_({event.variant_key for _, event in linked}),
            ),
        )
        .where(Experiment.id.in_({event.experiment_id for _, event in linked}))
    )
    experiments = {}
    variants = set()
    for experiment_id, status, flag_key, variant_key in result:
        experiments[experiment_id] = (status, flag_key)
        variants.add((experiment_id, variant_key))

    for index, event in linked:
        prefix = f"Событие {index + 1}"
        experiment = experiments.get(event.experiment_id)
        if experiment is None:
            raise EventLinkError(f"{prefix}: эксперимент не найден")
        status, flag_key = experiment
        if status != ExperimentStatus.RUNNING:
            raise EventLinkError(f"{prefix}: эксперимент не запущен")
        if event.flag_key != flag_key:
            raise EventLinkError(f"{prefix}: флаг не соответствует эксперименту")
        if (event.experiment_id, event.variant_key) not in variants:
            raise EventLinkError(f"{prefix}: вариант не принадлежит флагу эксперимента")
