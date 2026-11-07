from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.schemas.event import EventCreate
from src.services.statistics_service import (
    VariantCounts, compare_conversions, count_participants,
)


def test_known_proportion_comparison() -> None:
    """Известный пример даёт ожидаемые Z, p-value и интервал."""
    result = compare_conversions(VariantCounts(1000, 100), VariantCounts(1000, 150))
    assert result.z_statistic == pytest.approx(3.3806170189), (
        f"Ожидался Z около 3.3806, получено {result.z_statistic}"
    )
    assert result.p_value == pytest.approx(0.0007232327, rel=1e-5), (
        f"Ожидалось p около 0.0007232, получено {result.p_value}"
    )
    assert result.confidence_interval == pytest.approx((0.021094, 0.078906), abs=1e-6), (
        f"Неожиданный 95% интервал: {result.confidence_interval}"
    )
    assert result.is_significant, "Различие 10% и 15% должно быть значимым в примере"


@pytest.mark.parametrize("counts", [(0, 0), (10, 2), (100, 0), (100, 100)])
def test_insufficient_data_has_no_inference(counts: tuple[int, int]) -> None:
    """Пустая или вырожденная выборка не получает статистический вывод."""
    result = compare_conversions(VariantCounts(*counts), VariantCounts(100, 30))
    assert result.p_value is None and not result.is_significant, (
        f"Для выборки {counts} ожидалось отсутствие вывода, получено {result}"
    )


def test_equal_rates_and_sample_threshold() -> None:
    """Равные конверсии дают p=1, порог применяется к каждой группе."""
    a, b = VariantCounts(100, 20), VariantCounts(200, 40)
    result = compare_conversions(a, b)
    assert result.p_value == 1 and result.z_statistic == 0, (
        f"Для равных долей ожидались Z=0 и p=1, получено {result}"
    )
    assert not compare_conversions(a, b, min_sample_size=150).enough_data, (
        "Группа со 100 участниками не должна проходить порог 150"
    )


@pytest.mark.parametrize("counts", [(-1, 0), (10, 11), (True, 0), (10, -1)])
def test_invalid_counts_are_rejected(counts: tuple[int, int]) -> None:
    """Некорректные счётчики отклоняются."""
    with pytest.raises(ValueError):
        VariantCounts(*counts)


@pytest.mark.parametrize("alpha", [0, 1, float("nan"), float("inf")])
def test_invalid_alpha_is_rejected(alpha: float) -> None:
    """Уровень значимости должен быть конечным числом между 0 и 1."""
    with pytest.raises(ValueError):
        compare_conversions(VariantCounts(100, 20), VariantCounts(100, 30), alpha)


def test_reversing_groups_reverses_effect() -> None:
    """Перестановка групп меняет знак эффекта и сохраняет двустороннее p-value."""
    a, b = VariantCounts(1000, 100), VariantCounts(1000, 150)
    forward, reverse = compare_conversions(a, b), compare_conversions(b, a)
    assert reverse.z_statistic == -forward.z_statistic, (
        "При перестановке групп знак Z должен поменяться"
    )
    assert reverse.p_value == forward.p_value, (
        "При перестановке групп двустороннее p-value должно сохраняться"
    )


def test_participants_are_deduplicated_and_require_exposure() -> None:
    """Повторы, чужой эксперимент и конверсия до показа не искажают выборку."""
    experiment_id = uuid4()
    now = datetime.now(timezone.utc)

    def event(user, variant, kind, seconds=0, **overrides):
        return EventCreate(**{
            "event_type": kind, "flag_key": "checkout", "user_id": user,
            "experiment_id": experiment_id, "variant_key": variant,
            "event_name": "purchase", "timestamp": now + timedelta(seconds=seconds),
        } | overrides)

    impression = event("u1", "A", "impression")
    conversion = event("u1", "A", "conversion", 1)
    events = [
        impression, impression, conversion, conversion,
        event("u2", "B", "conversion"),
        event("u2", "B", "impression", 1),
        event("u3", "B", "conversion", 2),
        event("u4", "A", "impression", experiment_id=uuid4()),
    ]
    counts = count_participants(reversed(events), experiment_id, "purchase", "A", "B")
    assert counts == (VariantCounts(1, 1), VariantCounts(1, 0)), (
        f"Ожидался один участник на вариант и одна конверсия в A, получено {counts}"
    )
    with pytest.raises(ValueError):
        count_participants(
            [impression, event("u1", "B", "impression")],
            experiment_id, "purchase", "A", "B",
        )
