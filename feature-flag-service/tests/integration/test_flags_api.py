import os
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest

from src.services import flag_service
from src.utils.jwt_handler import create_access_token


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
        reason="Нужна явно включённая проверка локальной PostgreSQL",
    ),
]


def auth_headers(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user_id))}"}


async def test_flag_lifecycle_and_validation(
    api_client: httpx.AsyncClient, experiment_db: tuple[UUID, UUID, UUID],
) -> None:
    """Проверить запись владельцем, валидацию, конфликт ключа и мягкое удаление."""
    _, owner_id, other_id = experiment_db
    owner = auth_headers(owner_id)
    foreign = auth_headers(other_id)
    base = "/api/flags"
    path = f"{base}/release"
    created = await api_client.post(base, headers=owner, json={"key": "release"})
    assert created.status_code == 201, f"Флаг не создан: {created.text}"
    flag = await api_client.get(path)
    assert flag.status_code == 200 and flag.json() == created.json(), f"Не прочитан созданный флаг: {flag.text}"

    for method, url, headers, payload, expected in (
        ("POST", base, owner, {"key": "release"}, 409),
        ("PATCH", path, owner, {"key": "checkout"}, 409),
        ("PATCH", path, owner, {}, 400),
        ("PATCH", path, owner, {"enabled": None}, 400),
        ("PATCH", path, owner, {"rollout_percentage": 101}, 422),
        ("PATCH", path, foreign, {"enabled": True}, 403),
        ("DELETE", path, foreign, None, 403),
    ):
        response = await api_client.request(method, url, headers=headers, json=payload)
        assert response.status_code == expected, (
            f"{method} {url} с данными {payload} должен дать {expected}: {response.text}"
        )

    changed = await api_client.patch(path, headers=owner, json={"enabled": True, "rollout_percentage": 25})
    assert changed.status_code == 200 and changed.json()["enabled"], f"Флаг не включён: {changed.text}"
    assert changed.json()["rollout_percentage"] == 25, f"Процент rollout не сохранился: {changed.text}"
    deleted = await api_client.delete(path, headers=owner)
    assert deleted.status_code == 204 and not deleted.content, f"Флаг не удалён мягко: {deleted.text}"
    missing = await api_client.get(path)
    assert missing.status_code == 404, f"Архивный флаг доступен по ключу: {missing.text}"


async def test_flag_create_conflict_after_lookup(
    api_client: httpx.AsyncClient, experiment_db: tuple[UUID, UUID, UUID], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Преобразовать конфликт записи после успешной проверки ключа в HTTP 409."""
    _, owner_id, _ = experiment_db
    create = AsyncMock(side_effect=flag_service.ConflictError("Флаг с таким ключом уже существует"))
    monkeypatch.setattr(flag_service, "create_flag", create)
    response = await api_client.post("/api/flags", headers=auth_headers(owner_id), json={"key": "race"})
    assert create.await_count == 1, "Запрос не дошёл до записи флага после проверки ключа"
    assert response.status_code == 409, f"Конфликт записи должен давать 409: {response.text}"


async def test_variant_lifecycle_and_conflicts(
    api_client: httpx.AsyncClient, experiment_db: tuple[UUID, UUID, UUID],
) -> None:
    """Проверить чтение, изменение и удаление варианта и запреты для чужого флага."""
    flag_id, owner_id, other_id = experiment_db
    owner = auth_headers(owner_id)
    base = "/api/flags/checkout/variants"
    variants = {}
    for key in ("A", "B"):
        created = await api_client.post(base, headers=owner, json={"flag_id": str(flag_id), "variant_key": key})
        assert created.status_code == 201, f"Вариант {key} не создан: {created.text}"
        variants[key] = created.json()
    path = f"/api/flags/variants/{variants['A']['id']}"
    listed = await api_client.get(base)
    assert listed.status_code == 200 and {row["variant_key"] for row in listed.json()} == {"A", "B"}, (
        f"Список не содержит созданные варианты: {listed.text}"
    )
    found = await api_client.get(path)
    assert found.status_code == 200 and found.json() == variants["A"], f"Неверный вариант по UUID: {found.text}"

    for payload, expected in (({}, 400), ({"weight": None}, 400), ({"variant_key": "B"}, 409)):
        invalid = await api_client.patch(path, headers=owner, json=payload)
        assert invalid.status_code == expected, f"PATCH варианта с {payload} должен дать {expected}: {invalid.text}"
    foreign = await api_client.delete(path, headers=auth_headers(other_id))
    assert foreign.status_code == 403, f"Чужой разработчик смог удалить вариант: {foreign.text}"
    changed = await api_client.patch(path, headers=owner, json={"weight": 2.5})
    assert changed.status_code == 200 and changed.json()["weight"] == 2.5, f"Вес не сохранён: {changed.text}"
    deleted = await api_client.delete(path, headers=owner)
    assert deleted.status_code == 204 and not deleted.content, f"Вариант не удалён: {deleted.text}"

    for method, payload in (("GET", None), ("PATCH", {"weight": 1}), ("DELETE", None)):
        missing = await api_client.request(method, path, headers=owner, json=payload)
        assert missing.status_code == 404, f"{method} удалённого варианта должен дать 404: {missing.text}"


async def test_routes_return_not_found_for_missing_flag(
    api_client: httpx.AsyncClient, experiment_db: tuple[UUID, UUID, UUID],
) -> None:
    """Вернуть 404 для отсутствующего флага до изменения его дочерних ресурсов."""
    _, owner_id, _ = experiment_db
    base = "/api/flags/missing"
    for method, path, payload in (
        ("GET", base, None),
        ("PATCH", base, {"enabled": True}),
        ("DELETE", base, None),
        ("GET", f"{base}/variants", None),
        ("POST", f"{base}/variants", {"flag_id": str(uuid4()), "variant_key": "A"}),
        ("GET", f"{base}/targeting", None),
    ):
        response = await api_client.request(method, path, headers=auth_headers(owner_id), json=payload)
        assert response.status_code == 404, f"{method} {path} должен вернуть 404: {response.text}"
