# syntax=docker/dockerfile:1.7
# WooCommerce MCP server. Build from this directory:
#   docker build -t urumi/woocommerce-mcp:dev .
# Configuration (WC_*, DB_URL, MCP_*) comes from the environment at run time; nothing
# environment-specific or secret is baked into the image.

ARG PYTHON_IMAGE=python:3.14-slim-bookworm

# --- build: resolve locked dependencies into a virtualenv -------------------------------------
FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=2.3.2 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false \
    VIRTUAL_ENV=/opt/venv
RUN python -m venv /opt/poetry && /opt/poetry/bin/pip install "poetry==${POETRY_VERSION}" \
 && python -m venv "${VIRTUAL_ENV}"
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"
WORKDIR /src
# Dependencies first, so source edits do not invalidate this layer. poetry.lock pins every
# version and hash.
COPY pyproject.toml poetry.lock ./
RUN /opt/poetry/bin/poetry install --only main --no-root --no-ansi
COPY README.md ./
COPY src ./src
RUN pip install --no-deps . && pip uninstall -y pip

# --- runtime: interpreter, virtualenv, migrations; non-root ------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ARG UID=10001
RUN groupadd --system --gid "${UID}" app \
 && useradd --system --uid "${UID}" --gid app --no-create-home --shell /usr/sbin/nologin app
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_FORMAT=json \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
# alembic.ini + versions, for the migration job: `alembic upgrade head`
COPY alembic.ini ./
COPY alembic ./alembic
USER ${UID}:${UID}
EXPOSE 8000
# Liveness only (no dependencies); orchestrators use /readyz for traffic.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('MCP_PORT', '8000')}/healthz\", timeout=2)"]
# Exec form: uvicorn is PID 1 and receives SIGTERM directly for a graceful drain.
ENTRYPOINT ["woocommerce-mcp"]
