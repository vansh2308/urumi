# syntax=docker/dockerfile:1.7
# Platform API gateway. Build from this directory:
#   docker build -t urumi/gateway:dev .
# Configuration (DB_URL, AUTH_TOKEN_PEPPER, GATEWAY_*, UPSTREAM_*) comes from the environment
# at run time; nothing environment-specific or secret is baked into the image.

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

# --- helm: pinned binary for the store provisioner (checksums from get.helm.sh) ----------------
FROM ${PYTHON_IMAGE} AS helm
ARG TARGETARCH
ARG HELM_VERSION=4.3.0
ARG HELM_SHA256_AMD64=86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb
ARG HELM_SHA256_ARM64=31c5794dd55c66a51e6b7d2e2ac7a114ae8b1de41ff1d9ba51748ac973b06a08
ADD https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz /tmp/helm.tgz
RUN case "${TARGETARCH}" in \
      amd64) sum="${HELM_SHA256_AMD64}" ;; \
      arm64) sum="${HELM_SHA256_ARM64}" ;; \
      *) echo "unsupported architecture ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && echo "${sum}  /tmp/helm.tgz" | sha256sum -c - \
 && tar -xzf /tmp/helm.tgz -C /tmp \
 && install -m 0755 "/tmp/linux-${TARGETARCH}/helm" /usr/local/bin/helm

# --- the urumi-store chart for the provisioner --------------------------------------------------
# Empty unless the build passes it (deploy/scripts/deploy-local.sh does):
#   --build-context store-chart=deploy/helm/urumi-store
# after deploy/scripts/package-store-chart.sh has put its dependencies into charts/.
FROM scratch AS store-chart

# --- runtime: interpreter, virtualenv, migrations; non-root ------------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ARG UID=10001
RUN groupadd --system --gid "${UID}" app \
 && useradd --system --uid "${UID}" --gid app --no-create-home --shell /usr/sbin/nologin app
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_FORMAT=json \
    GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=8100
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
# alembic.ini + versions, for the migration job (`alembic upgrade head`). The operator CLI
# `gateway-admin` is installed in the virtualenv.
COPY alembic.ini ./
COPY alembic ./alembic
# The provisioner (`gateway-provisioner`, same image) installs stores with these; the gateway
# itself never calls Helm.
COPY --from=helm /usr/local/bin/helm /usr/local/bin/helm
COPY --from=store-chart / /opt/urumi/charts/urumi-store/
USER ${UID}:${UID}
EXPOSE 8100
# Liveness only (no dependencies); orchestrators use /health/ready for traffic.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('GATEWAY_PORT', '8100')}/health/live\", timeout=2)"]
# Exec form: uvicorn is PID 1 and receives SIGTERM directly for a graceful drain.
ENTRYPOINT ["gateway"]
