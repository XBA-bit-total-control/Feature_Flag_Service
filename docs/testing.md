# Тестирование

Тесты находятся в `feature-flag-service/tests/`. Unit-тесты работают без
запущенных сервисов. Интеграционные тесты используют настоящие PostgreSQL и
MongoDB там, где проверяют запросы, ограничения или запись документов. FastAPI
вызывается через HTTPX `ASGITransport` внутри процесса. Запускать Uvicorn,
RabbitMQ, Celery worker и Beat для тестов не требуется. Внешний LLM API
не вызывается, ключ для тестов не нужен.

## 1. Установить зависимости и запустить unit-тесты

Нужны Python 3.12 и `uv`. Откройте PowerShell 7 в корне репозитория.
Для интеграционного прогона дополнительно нужен Docker с `docker compose`.

```powershell
Set-Location feature-flag-service
uv sync --locked --python 3.12
uv run --locked python -m pytest tests/unit -q
```

В текущем наборе ожидается `107 passed`.

Команды `uv`, `pytest` и `alembic` выполняются из `feature-flag-service/`, где
находятся `pyproject.toml`, `uv.lock` и `alembic.ini`. Вызов `python -m pytest`
добавляет текущую директорию в путь импорта. При прямом запуске `pytest`
на Windows возможна ошибка `ModuleNotFoundError: No module named 'src'`.
`.env` для тестов создавать не требуется. Если он уже существует, переменные
оболочки из следующего раздела имеют приоритет над его настройками БД.

Для точечной проверки передайте путь к файлу или путь с именем теста после `::`.
`-v` выводит названия сценариев, `-x` останавливается на первой ошибке,
`--collect-only` показывает, какие тесты найдёт pytest.

```powershell
uv run --locked python -m pytest tests/unit/test_statistics_service.py -v
uv run --locked python -m pytest tests/unit/test_statistics_service.py::test_known_proportion_comparison -v
uv run --locked python -m pytest tests/unit tests/integration --collect-only -q
```

## 2. Поднять отдельные БД для интеграционных тестов

Откройте новую вкладку PowerShell в корне репозитория. Запустите только
PostgreSQL и MongoDB под отдельным именем Compose-проекта. Так данные тестов
будут храниться в отдельных томах. Если указанные порты заняты, выберите
свободные и сохраните соответствие между портами Compose и Python.

```powershell
$env:POSTGRES_HOST_PORT = '15440'
$env:MONGO_HOST_PORT = '27030'
docker compose -p ffs-tests up -d --wait --wait-timeout 150 postgres mongo
docker compose -p ffs-tests ps
```

Дождитесь `healthy` у обеих БД. В той же вкладке настройте подключение и
примените миграции на тестовой PostgreSQL. Значения пользователя, пароля и
имени базы ниже соответствуют локальному Compose.

```powershell
Set-Location feature-flag-service
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = $env:POSTGRES_HOST_PORT
$env:POSTGRES_USER = 'postgres'
$env:POSTGRES_PASSWORD = 'postgres'
$env:POSTGRES_DB = 'feature_flags'
$env:MONGO_HOST = '127.0.0.1'
$env:MONGO_PORT = $env:MONGO_HOST_PORT
$env:MONGO_DB = 'events'
$env:JWT_SECRET_KEY = 'local-tests-only-jwt-key'
$env:LLM_API_KEY = ''
uv run --locked python -m alembic upgrade head
```

Миграции проверяют подготовку тестовой базы с чистого состояния, как в CI.
Применяйте их только к тестовой БД. Фикстуры PostgreSQL создают собственные
схемы из ORM-моделей и откатывают тестовые транзакции либо удаляют временную
схему. MongoDB-тесты используют временные базы или коллекции и очищают их после
выполнения. Индексы проверяются и создаются в соответствующем тесте.

## 3. Запустить интеграционные тесты

В той же вкладке, из `feature-flag-service/`, включите оба набора и запустите
полный прогон. Переменные включаются только значением `1`. `-rs` показывает
причины пропуска, `--tb=short` сокращает трассировку ошибок.

```powershell
$env:RUN_POSTGRES_INTEGRATION = '1'
$env:RUN_MONGO_INTEGRATION = '1'
uv run --locked python -m pytest tests/unit tests/integration -q -rs --tb=short
```

| __Что нужно запустить__              | __Аргументы после `uv run --locked python -m pytest`__ | __Какие сервисы и флаги нужны__                                                                                          |
|--------------------------------------|--------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------|
| __Только unit-тесты__                | `tests/unit -q`                                        | БД и флаги не нужны.                                                                                                     |
| __Только интеграционная директория__ | `tests/integration -q -rs`                             | Для всех сценариев нужны обе БД и оба флага.                                                                             |
| __Проверка аутентификации__          | `tests/integration/test_auth_api.py -v`                | PostgreSQL и `RUN_POSTGRES_INTEGRATION=1`.                                                                               |
| __Проверка хранения в MongoDB__      | `tests/integration/test_event_service_mongo.py -v`     | MongoDB и `RUN_MONGO_INTEGRATION=1`.                                                                                     |
| __Проверка HTTP-приёма событий__     | `tests/integration/test_events_api.py -v -rs`          | MongoDB и `RUN_MONGO_INTEGRATION=1` для сценария реальной записи. Проверки валидации и ошибок записи используют подмены. |

Если флаги не выставлены, pytest пропустит сценарии, помеченные требованием
реальной БД. Зелёный итог с `skipped` означает, что эти сценарии не выполнялись.
Если флаг включён, а соединения нет, проверьте `docker compose -p ffs-tests ps`,
переменные `POSTGRES_*` и `MONGO_*` и каталог запуска.

### Отдельный запуск тестов MongoDB

Если нужна только проверка MongoDB, начните в корне репозитория и запустите
один сервис. Зависимости Python должны быть установлены по шагу 1.
Миграции PostgreSQL для этих двух файлов не нужны.

```powershell
$env:MONGO_HOST_PORT = '27030'
docker compose -p ffs-tests up -d --wait --wait-timeout 150 mongo
Set-Location feature-flag-service
$env:MONGO_HOST = '127.0.0.1'
$env:MONGO_PORT = $env:MONGO_HOST_PORT
$env:MONGO_DB = 'events'
$env:RUN_MONGO_INTEGRATION = '1'
uv run --locked python -m pytest tests/integration/test_event_service_mongo.py tests/integration/test_events_api.py -v -rs
```

В текущем наборе ожидается `9 passed`.

### Остановить тестовую инфраструктуру

После тестов и замера покрытия вернитесь в корень и удалите инфраструктуру
`ffs-tests` вместе с её тестовыми томами. Закройте вкладку, чтобы сбросить
переменные тестового окружения.

```powershell
Set-Location ..
docker compose -p ffs-tests down --volumes
```

## Что проверяет каждый файл

### Unit-тесты:

| __Файл__                                                                                        | __Проверки__                                                                                                                                                                       |
|-------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| __[test_hashing.py](../feature-flag-service/tests/unit/test_hashing.py)__                       | Детерминированность SHA-256-корзины, диапазон `0–99`, изменение входов хеша, пустые, длинные и Unicode-идентификаторы.                                                             |
| __[test_resolve_logic.py](../feature-flag-service/tests/unit/test_resolve_logic.py)__           | Выбор варианта по весам, границы корзин, равные, дробные, нулевые и крайние веса, пустой список.                                                                                   |
| __[test_resolve_service.py](../feature-flag-service/tests/unit/test_resolve_service.py)__       | Rollout на границах `0` и `100`, стабильное A/B-назначение, требование запущенного эксперимента и совместимость `user_id` с `user_identifier`.                                     |
| __[test_targeting_service.py](../feature-flag-service/tests/unit/test_targeting_service.py)__   | Операторы таргетинга, проверка типов без преобразования, отсутствующие атрибуты и недопустимые настройки правила.                                                                  |
| __[test_event_service.py](../feature-flag-service/tests/unit/test_event_service.py)__           | Формат событий, UTC, UUID, проверка входных и служебных полей, одиночная и пакетная вставка с подменой коллекции, повторы и ошибка записи.                                         |
| __[test_statistics_service.py](../feature-flag-service/tests/unit/test_statistics_service.py)__ | Z-тест, p-value и интервал на известных данных, недостаточная выборка, некорректные входы, смена знака эффекта, подсчёт уникальных участников с показом.                           |
| __[test_report_service.py](../feature-flag-service/tests/unit/test_report_service.py)__         | Тело запроса Chat Completions, успешный ответ, ограничение повторов, fallback при сбое или отсутствии ключа и шаблон при пустых данных. HTTP подменён через `httpx.MockTransport`. |
| __[test_celery_tasks.py](../feature-flag-service/tests/unit/test_celery_tasks.py)__             | Запуск задач через `Task.apply()`, повторы временных сбоев и их лимит, сохранение UUID при повторе, отклонение неверного UUID и подбор ожидающих отчётов.                          |
| __[test_health.py](../feature-flag-service/tests/unit/test_health.py)__                         | Liveness и readiness, ошибка драйвера PostgreSQL, счётчик `/resolve`, отсутствие метрики при сбое её источника и запись шаблона маршрута в лог.                                    |

### Интеграционные тесты:

| __Файл__                                                                                                 | __Проверки и зависимости__                                                                                                                                                                                                                               |
|----------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| __[test_auth_api.py](../feature-flag-service/tests/integration/test_auth_api.py)__                       | PostgreSQL. Регистрация, вход, профиль, дубликаты email, ошибки ввода и токенов, защита операций смены роли и сохранение роли.                                                                                                                           |
| __[test_flags_api.py](../feature-flag-service/tests/integration/test_flags_api.py)__                     | PostgreSQL. Создание, чтение, изменение и удаление флагов и вариантов, права доступа, конфликты ключей, несовпадение `flag_id` с URL и ответы для отсутствующих ресурсов.                                                                                |
| __[test_experiment_service.py](../feature-flag-service/tests/integration/test_experiment_service.py)__   | PostgreSQL. Список экспериментов для администратора, запрет создания и запуска после архивации флага, обработка реальных нарушений уникальности, внешнего ключа и `CHECK`.                                                                               |
| __[test_experiments_api.py](../feature-flag-service/tests/integration/test_experiments_api.py)__         | PostgreSQL. Жизненный цикл эксперимента, права, аудит, мягкое удаление, пагинация, таргетинг и `/resolve`. В сценариях с MongoDB также проверяет путь от показа до расчёта, связи событий, сохранение статистики при очистке, API результатов и отчётов. |
| __[test_event_service_mongo.py](../feature-flag-service/tests/integration/test_event_service_mongo.py)__ | MongoDB. Реальная одиночная и пакетная запись, BSON-даты, UUID, вложенные метаданные, начальное состояние обработки и пустой пакет.                                                                                                                      |
| __[test_events_api.py](../feature-flag-service/tests/integration/test_events_api.py)__                   | MongoDB для записи и проверки индексов. Тест вызывает обработку сохранённых событий напрямую и проверяет `processed`. Остальные сценарии проверяют HTTP-валидацию и ответы при ошибках записи с подменами.                                               |
| __[test_celery_workflows.py](../feature-flag-service/tests/integration/test_celery_workflows.py)__       | PostgreSQL, а для аналитики также MongoDB. Проверяет пересчёт только запущенных экспериментов, конфликт вариантов, автоматический шаблон без дублей, выполнение отчёта по ID, отсутствующий снимок и порядок первых 100 заданий. Ответ LLM подменён.     |

Интеграционный [conftest.py](../feature-flag-service/tests/integration/conftest.py)
подготавливает тестовых владельцев, флаг, изолированную схему PostgreSQL и
HTTPX-клиент. Это общие фикстуры.

## Покрытие проекта

Полный прогон с PostgreSQL и MongoDB дал **140 пройденных тестов**. Замер
от 10 ноября 2026 года охватывает 57 Python-файлов в `src/`. Тесты, миграции,
скрипты и конфигурация инфраструктуры исключены из расчёта.

| __Показатель__                        |  __Покрытие__ |  __Выполнено / всего__ |
|---------------------------------------|--------------:|-----------------------:|
| __Исполняемые строки__                |        95,41% |            1768 / 1853 |
| __Ветви__                             |        86,34% |              297 / 344 |
| __Сводный показатель `coverage.py`__  |        93,99% |            2065 / 2197 |

Сводный процент учитывает строки и ветви вместе. Для повторного замера
оставьте БД и оба интеграционных флага активными. Из `feature-flag-service/`
установите `coverage.py` отдельно от зависимостей приложения, чтобы не менять
`pyproject.toml` и `uv.lock`.

```powershell
uv pip install --target .venv/coverage-tools 'coverage==7.17.2'
$env:PYTHONPATH = (Resolve-Path .venv/coverage-tools).Path
$env:COVERAGE_FILE = '.venv/.coverage.full'
uv run --locked python -m coverage run --branch --concurrency=greenlet --source=src -m pytest tests/unit tests/integration -q
uv run --locked python -m coverage report --precision=2 -m
uv run --locked python -m coverage json -o .venv/coverage-full.json
```

`--branch` включает измерение ветвей. `--concurrency=greenlet` учитывает
исполнение SQLAlchemy при переходах между асинхронным и синхронным кодом.
В текстовом отчёте `Missing` показывает пропущенные строки и переходы. В JSON
раздельные проценты доступны в `totals.percent_statements_covered` и
`totals.percent_branches_covered`.

## GitHub Actions

[Workflow CI](../.github/workflows/ci.yml) запускается на `push` и
`pull_request`. На Ubuntu он поднимает PostgreSQL и MongoDB через Compose,
устанавливает зависимости из `uv.lock` с Python 3.12, применяет миграции,
включает оба интеграционных флага и выполняет полный pytest-прогон. Затем
собирает образ приложения через `docker compose build init`. Шаг очистки
выполняется независимо от результата и удаляет тестовые контейнеры и тома.
Процент покрытия измеряется отдельной командой. Текущий workflow порог
покрытия не задаёт и отчёт не публикует.
