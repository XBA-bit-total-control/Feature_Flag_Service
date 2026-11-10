# Запуск Feature Flag Service

После запуска будут доступны API, обработчик событий Celery и планировщик Beat.
Для первого знакомства используйте Docker Compose. Для работы с кодом можно
запустить Python-процессы на хосте, оставив БД и брокер в контейнерах.

## Docker Compose

Нужны Git, запущенный Docker Engine с поддержкой Linux-контейнеров и команда
`docker compose`. Сборка загружает базовые образы и зависимости из `uv.lock`.
Команды HTTP в этом руководстве рассчитаны на PowerShell 7.

```console
git clone https://github.com/XBA-bit-total-control/Feature_Flag_Service.git
cd Feature_Flag_Service
docker compose up --build -d --wait --wait-timeout 180
docker compose ps -a
```

Одноразовый сервис `init` применяет миграции PostgreSQL и создаёт индексы MongoDB.
В `docker compose ps -a` для него ожидается `Exited (0)`, для API — `healthy`,
для worker и Beat — `Up`. Если `init` завершился с ошибкой, начните с
`docker compose logs init`.

```powershell
$api = 'http://127.0.0.1:8000'
Invoke-RestMethod "$api/api/health/ready" | ConvertTo-Json
```

Readiness возвращает HTTP `200`, `status: ok` и статусы `ok` для `postgres` и
`mongo`. При недоступности БД возвращается `503` с указанием зависимости.
Интерактивная схема API доступна в [Swagger UI](http://127.0.0.1:8000/docs)
и [ReDoc](http://127.0.0.1:8000/redoc).

### Порты и параметры окружения

Порты опубликованы на `127.0.0.1`. Если какой-то из них занят, укажите свободный
порт в переменной оболочки или в `.env` рядом с `docker-compose.yml`.
Это меняет адрес для хоста. Внутри сети Compose контейнеры используют имена
сервисов и штатные порты.

| __Сервис__                 | __Порт по умолчанию__ | __Переменная хоста__    |
|----------------------------|----------------------:|-------------------------|
| __FastAPI__                |                  8000 | `API_HOST_PORT`         |
| __PostgreSQL__             |                  5432 | `POSTGRES_HOST_PORT`    |
| __MongoDB__                |                 27017 | `MONGO_HOST_PORT`       |
| __RabbitMQ AMQP__          |                  5672 | `RABBITMQ_HOST_PORT`    |
| __RabbitMQ Management UI__ |                 15672 | `RABBITMQ_UI_HOST_PORT` |
| __Redis__                  |                  6379 | `REDIS_HOST_PORT`       |

Например, чтобы освободить стандартный порт локальной PostgreSQL для другого
процесса, задайте `POSTGRES_HOST_PORT=15432` в корневом `.env`. Если меняете
`API_HOST_PORT`, подставьте новый порт в `$api`, Swagger UI и ReDoc.

Для локального Compose доступны `JWT_SECRET_KEY`, `LOG_LEVEL` и `LLM_*`.
Ключ LLM для запуска и [демонстрации](demo.md) не требуется. Секреты передавайте
через переменные окружения или отдельный файл вне репозитория. Для второго
способа укажите путь в команде запуска.

```console
docker compose --env-file /path/to/local.env up --build -d --wait --wait-timeout 180
```

Файл `feature-flag-service/.env` читает приложение при прямом запуске Python
из своей рабочей директории. Compose берёт переменные из окружения оболочки
и корневого `.env` либо указанного `--env-file`. Если в окружении уже задан
`LLM_API_KEY`, для офлайн-демонстрации оставьте его пустым в окружении Compose.

### Первый администратор

Новая учётная запись получает роль `viewer`. На новой БД оператор назначает
первого администратора напрямую в PostgreSQL. Сначала зарегистрируйте аккаунт.
Запомните пароль для [сценария демонстрации](demo.md).

```powershell
$adminEmail = 'admin@example.com'
$adminPassword = Read-Host 'Пароль администратора (от 8 символов, до 72 байт UTF-8)' -MaskInput
Invoke-RestMethod -Method Post -Uri "$api/auth/register" -ContentType 'application/json' -Body (@{
    email = $adminEmail
    password = $adminPassword
} | ConvertTo-Json)
```

Ожидается HTTP `201` и `role: viewer`. Откройте `psql` из корня репозитория.

```console
docker compose exec postgres psql -U postgres -d feature_flags
```

В `psql` подставьте email зарегистрированного аккаунта. `UPDATE 1` означает,
что роль назначена. `\q` закрывает сеанс.

```sql
UPDATE users SET role = 'ADMIN' WHERE email = 'admin@example.com';
```

Для проверки выполните вход и прочитайте профиль.

```powershell
$login = Invoke-RestMethod -Method Post -Uri "$api/auth/login" -ContentType 'application/json' -Body (@{
    email = $adminEmail
    password = $adminPassword
} | ConvertTo-Json)
Invoke-RestMethod -Uri "$api/auth/me" -Headers @{ Authorization = "Bearer $($login.access_token)" }
```

Профиль вернёт `role: admin`. Дальше можно пройти [полный сценарий A/B](demo.md).
`docker compose down` останавливает стек с сохранением данных в томах.

## Прямой запуск Python для разработки

Нужны Python 3.12 или новее и `uv`. В корне поднимите только зависимости.
Если эти сервисы уже запущены на хосте, используйте их адреса и порты в `.env`.

```powershell
docker compose up -d --wait postgres mongo rabbitmq
Set-Location feature-flag-service
uv sync --locked
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

В `feature-flag-service/.env` настройте `POSTGRES_*`, `MONGO_*` и
`CELERY_BROKER_URL` под адреса хоста. Для Compose с портами по умолчанию
подходят `localhost` и стандартные порты из `.env.example`. При переопределении
`RABBITMQ_HOST_PORT` измените порт именно в `CELERY_BROKER_URL`. Для запуска
без провайдера оставьте `LLM_API_KEY=` вместо заглушки из примера.

Миграции выполняйте для предназначенной локальной базы. Примените их, создайте
индексы событий и запустите API из `feature-flag-service/`.

```powershell
uv run --locked alembic upgrade head
uv run --locked python -m src.event_indexes
uv run --locked uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload
```

Для фоновых задач в двух отдельных терминалах из той же директории запустите
worker и Beat. Пример worker использует `--pool=solo` для локального запуска
на Windows.

```powershell
uv run --locked celery -A src.celery_app:app worker --pool=solo -Q events,analytics,reports --loglevel=INFO
```

```powershell
uv run --locked celery -A src.celery_app:app beat --loglevel=INFO
```

Проверьте API запросом к `/api/health/ready`. Для развёртывания в Kubernetes
используйте [инструкцию для minikube](../k8s/README.md).
