from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from itertools import chain
from math import erfc, sqrt
from statistics import NormalDist
from uuid import UUID

import numpy as np

from src.schemas.event import EventCreate


@dataclass(frozen=True)
class VariantCounts:
    """Число уникальных участников и участников с конверсией."""

    sample_size: int
    conversions: int

    def __post_init__(self) -> None:
        if type(self.sample_size) is not int or type(self.conversions) is not int:
            raise ValueError("Размер выборки и число конверсий должны быть целыми")
        if not 0 <= self.conversions <= self.sample_size:
            raise ValueError("Конверсии должны быть в диапазоне от 0 до размера выборки")

    @property
    def conversion_rate(self) -> float | None:
        return self.conversions / self.sample_size if self.sample_size else None


@dataclass(frozen=True)
class ComparisonResult:
    """Двустороннее сравнение конверсий с разницей treatment минус control."""

    difference: float | None
    z_statistic: float | None
    p_value: float | None
    confidence_interval: tuple[float, float] | None
    is_significant: bool
    enough_data: bool


@dataclass
class ParticipantEvents:
    """Минимальное состояние участника для подсчёта показа и конверсии."""

    user_id: str
    variant_key: str
    impression_at: datetime | None = None
    conversion_at: datetime | None = None


def summarize_participants(
    events: Iterable[EventCreate], experiment_id: UUID, primary_metric: str,
) -> Iterable[ParticipantEvents]:
    """Свернуть повторы до первого показа и последней целевой конверсии."""
    participants: dict[tuple[str, str], ParticipantEvents] = {}
    for event in events:
        if event.experiment_id != experiment_id:
            continue
        key = (event.user_id, event.variant_key or "")
        participant = participants.setdefault(key, ParticipantEvents(*key))
        if event.event_type == "impression":
            participant.impression_at = min(participant.impression_at or event.timestamp, event.timestamp)
        if event.event_type == "conversion" and event.event_name == primary_metric:
            participant.conversion_at = max(participant.conversion_at or event.timestamp, event.timestamp)
    return participants.values()


def count_participants(
    events: Iterable[EventCreate],
    experiment_id: UUID,
    primary_metric: str,
    control_key: str,
    treatment_key: str,
    *, archived: Iterable[ParticipantEvents] = (),
) -> tuple[VariantCounts, VariantCounts]:
    """Посчитать участников с показом и последующей целевой конверсией."""
    if not primary_metric or not control_key or not treatment_key:
        raise ValueError("Метрика и ключи вариантов не должны быть пустыми")
    if control_key == treatment_key:
        raise ValueError("Ключи control и treatment должны различаться")
    assignments = {}
    impressions = {}
    conversions = {}
    current = summarize_participants(events, experiment_id, primary_metric)
    for participant in chain(archived, current):
        user_id, variant_key = participant.user_id, participant.variant_key
        if variant_key not in (control_key, treatment_key):
            raise ValueError("В событии указан вариант вне сравниваемой пары")
        if user_id in assignments and assignments[user_id] != variant_key:
            raise ValueError("Один участник оказался в разных вариантах эксперимента")
        assignments[user_id] = variant_key
        key = (user_id, variant_key)
        if (timestamp := participant.impression_at) is not None:
            impressions[key] = min(impressions.get(key, timestamp), timestamp)
        if (timestamp := participant.conversion_at) is not None:
            conversions[key] = max(conversions.get(key, timestamp), timestamp)
    counts = []
    for variant in (control_key, treatment_key):
        participants = [key for key in impressions if key[1] == variant]
        successes = sum(
            key in conversions and conversions[key] >= impressions[key]
            for key in participants
        )
        counts.append(VariantCounts(len(participants), successes))
    return counts[0], counts[1]


def compare_conversions(
    control: VariantCounts,
    treatment: VariantCounts,
    alpha: float = 0.05,
    min_sample_size: int | None = None,
) -> ComparisonResult:
    """Рассчитать Z-тест долей и приближённый интервал разницы конверсий."""
    if not np.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("alpha должен быть между 0 и 1")
    if min_sample_size is not None and (
        type(min_sample_size) is not int or min_sample_size <= 0
    ):
        raise ValueError("Минимальная выборка должна быть положительным целым")
    if not control.sample_size or not treatment.sample_size:
        return ComparisonResult(None, None, None, None, False, False)

    sizes = np.array([control.sample_size, treatment.sample_size], dtype=float)
    successes = np.array([control.conversions, treatment.conversions], dtype=float)
    rates = successes / sizes
    difference = float(rates[1] - rates[0])
    enough_data = bool(
        np.all(successes >= 5)
        and np.all(sizes - successes >= 5)
        and (min_sample_size is None or np.all(sizes >= min_sample_size))
    )
    if not enough_data:
        return ComparisonResult(difference, None, None, None, False, False)

    pooled = float(np.sum(successes) / np.sum(sizes))
    standard_error = sqrt(pooled * (1 - pooled) * float(np.sum(1 / sizes)))
    z_statistic = difference / standard_error
    p_value = erfc(abs(z_statistic) / sqrt(2))
    margin = -NormalDist().inv_cdf(alpha / 2) * sqrt(
        float(np.sum(rates * (1 - rates) / sizes))
    )
    interval = (max(-1.0, difference - margin), min(1.0, difference + margin))
    return ComparisonResult(
        difference, z_statistic, p_value, interval, p_value < alpha, True
    )
