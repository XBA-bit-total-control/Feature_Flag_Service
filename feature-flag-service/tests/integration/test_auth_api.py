import os
from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest

from src.database import get_async_session
from src.main import app
from src.models.user import User, UserRole
from src.utils.jwt_handler import create_access_token


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
        reason="Нужна явно включённая проверка локальной PostgreSQL",
    ),
]


async def test_register_login_and_profile(api_client: httpx.AsyncClient) -> None:
    """Проверить регистрацию, выдачу JWT и получение профиля по этому токену."""
    credentials = {"email": "auth@example.com", "password": "test-password-123"}
    registered = await api_client.post("/auth/register", json=credentials)
    assert registered.status_code == 201, f"Регистрация завершилась ошибкой: {registered.text}"
    profile = registered.json()
    assert profile == {"id": profile["id"], "email": credentials["email"], "role": "viewer"}, (
        f"Профиль новой учётной записи содержит неверную роль или лишние поля: {profile.keys()}"
    )
    duplicate = await api_client.post("/auth/register", json=credentials)
    assert duplicate.status_code == 400, f"Повторный email принят: {duplicate.status_code}"
    logged_in = await api_client.post("/auth/login", json=credentials)
    assert logged_in.status_code == 200, f"Верные данные не приняты: {logged_in.status_code}"
    token = logged_in.json()
    assert token["token_type"] == "bearer", f"Неверный тип токена: {token['token_type']}"
    current = await api_client.get("/auth/me", headers={"Authorization": f"Bearer {token['access_token']}"})
    assert current.status_code == 200 and current.json() == profile, (
        f"Полученный JWT не вернул профиль зарегистрированного пользователя: {current.status_code}"
    )
    wrong_password = await api_client.post("/auth/login", json={**credentials, "password": "wrong-password"})
    unknown_email = await api_client.post("/auth/login", json={**credentials, "email": "unknown@example.com"})
    assert wrong_password.status_code == unknown_email.status_code == 401, (
        f"Неверные данные должны давать 401: {wrong_password.status_code}, {unknown_email.status_code}"
    )
    assert wrong_password.json() == unknown_email.json(), "Ошибка входа раскрывает, существует ли email"


async def test_rejects_invalid_registration_and_tokens(
    api_client: httpx.AsyncClient, experiment_db: tuple[UUID, UUID, UUID],
) -> None:
    """Отклонить ошибочные данные регистрации и JWT без действующего пользователя."""
    _, owner_id, _ = experiment_db
    for email, password in (("not-an-email", "test-password"), ("auth@example.com", "я" * 37)):
        invalid = await api_client.post("/auth/register", json={"email": email, "password": password})
        assert invalid.status_code == 422, f"Неверный email или пароль принят: {invalid.status_code}"

    tokens = {
        "без токена": None,
        "повреждённый": "invalid-token",
        "просроченный": create_access_token(str(owner_id), expires_delta=timedelta(minutes=-1)),
        "без пользователя в БД": create_access_token(str(uuid4())),
    }
    for name, token in tokens.items():
        headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
        response = await api_client.get("/auth/me", headers=headers)
        assert response.status_code == 401, f"Запрос с вариантом «{name}» вернул {response.status_code} вместо 401"


async def test_admin_role_guards(
    api_client: httpx.AsyncClient, experiment_db: tuple[UUID, UUID, UUID],
) -> None:
    """Разрешить смену роли администратору и защитить его от понижения через API."""
    _, developer_id, admin_id = experiment_db
    async for db in app.dependency_overrides[get_async_session]():
        admin = await db.get(User, admin_id)
        admin.role = UserRole.ADMIN

    admin_headers = {"Authorization": f"Bearer {create_access_token(str(admin_id))}"}
    developer_headers = {"Authorization": f"Bearer {create_access_token(str(developer_id))}"}
    path = f"/auth/users/{developer_id}/role"
    for target, headers, role, expected in (
        (path, developer_headers, "admin", 403),
        (f"/auth/users/{uuid4()}/role", admin_headers, "developer", 404),
        (f"/auth/users/{admin_id}/role", admin_headers, "viewer", 409),
        (path, admin_headers, "unknown", 422),
    ):
        response = await api_client.patch(target, headers=headers, json={"role": role})
        assert response.status_code == expected, (
            f"При назначении {role} через {target} ожидался HTTP {expected}, получен {response.status_code}"
        )
    changed = await api_client.patch(path, headers=admin_headers, json={"role": "viewer"})
    assert changed.status_code == 200 and changed.json()["role"] == "viewer", (
        f"Администратор не смог изменить роль разработчика: {changed.text}"
    )
    current = await api_client.get("/auth/me", headers=developer_headers)
    assert current.status_code == 200 and current.json()["role"] == "viewer", (
        f"Ранее выданный JWT не отражает новую роль: {current.text}"
    )
