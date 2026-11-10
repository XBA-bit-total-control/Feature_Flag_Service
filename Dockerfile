FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.12-slim-bookworm AS python-base

FROM python-base AS dependencies
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /build
COPY feature-flag-service/pyproject.toml feature-flag-service/uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

FROM python-base AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
COPY feature-flag-service/src ./src
COPY feature-flag-service/alembic ./alembic
COPY feature-flag-service/alembic.ini ./alembic.ini
USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
