import pytest
from pydantic import ValidationError

from src.models.flag_targeting import FlagTargeting
from src.schemas.flag_targeting import FlagTargetingWrite
from src.services.targeting_service import matches_rule


@pytest.mark.parametrize(("operator", "values", "attributes", "expected"), [
    ("eq", [1], {"age": True}, False),
    ("ne", [18], {}, False),
    ("in", ["RU", "KZ"], {"age": "KZ"}, True),
    ("not_in", ["RU"], {"age": "KZ"}, True),
    ("not_in", ["RU"], {"age": None}, False),
    ("gt", [18], {"age": 19}, True),
    ("lt", [18], {"age": 18}, False),
    ("gte", [18], {"age": 18}, True),
    ("gte", [18], {"age": "18"}, False),
    ("lte", [18], {"age": 18.0}, True),
])
def test_matches_rule(operator, values, attributes, expected) -> None:
    """Проверить операторы, границы и сравнение без приведения типов."""
    rule = FlagTargeting(attribute="age", operator=operator, values=values, is_enabled=True)
    assert matches_rule(rule, attributes) is expected, (
        f"Для {operator} {values} и атрибутов {attributes} ожидалось {expected}"
    )


@pytest.mark.parametrize(("operator", "values"), [
    ("eq", []), ("eq", [None]), ("eq", [1, 2]), ("eq", [[1]]),
    ("in", [1, 1.0]), ("gte", ["18"]), ("lt", [True]),
    ("lt", [float("inf")]), ("unknown", [1]),
])
def test_invalid_rule(operator, values) -> None:
    """Отклонить некорректные значения и безопасно обработать старую запись."""
    with pytest.raises(ValidationError):
        FlagTargetingWrite(attribute="age", operator=operator, values=values)
    rule = FlagTargeting(attribute="age", operator=operator, values=values, is_enabled=True)
    assert not matches_rule(rule, {"age": 18}), (
        f"Некорректное сохранённое правило {operator} {values} пропустило пользователя"
    )
