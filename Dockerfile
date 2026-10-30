# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da AS frontend-builder

WORKDIR /build
RUN npm install --global pnpm@10.34.9
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./frontend/
RUN cd frontend && pnpm install --frozen-lockfile

COPY frontend ./frontend
RUN cd frontend && pnpm run build


FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS python-builder

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH=/opt/venv/bin:$PATH

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build

COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./backend/
RUN uv sync --project backend --locked --no-dev --extra sqlserver --no-build --python /usr/local/bin/python \
    && sha256sum backend/pyproject.toml backend/uv.lock backend/.python-version \
        | sed 's|  backend/|  /app/backend/|' > /opt/python-project.sha256



FROM python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    APP_ENV=production \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    PYTHONPATH=/app/backend \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# Frozen Debian repositories keep system dependencies tied to this release.
RUN printf 'Types: deb\nURIs: https://snapshot.debian.org/archive/debian/20261028T000000Z\nSuites: bookworm bookworm-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n\nTypes: deb\nURIs: https://snapshot.debian.org/archive/debian-security/20261028T000000Z\nSuites: bookworm-security\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\nCheck-Valid-Until: no\n' > /etc/apt/sources.list.d/debian.sources

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        libgssapi-krb5-2 \
        tzdata \
        unixodbc \
    && curl -fsSLo /tmp/packages-microsoft-prod.deb \
        https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && echo "8434dcb8c346dc95fbd63dbece056c343704590b58b6a5c323d39acf52bf0b48  /tmp/packages-microsoft-prod.deb" | sha256sum -c - \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm -f /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18=18.6.2.1-1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=python-builder /opt/venv /opt/venv
COPY --from=python-builder /opt/python-project.sha256 /opt/python-project.sha256
COPY --chmod=755 scripts/linux/docker_entrypoint.sh /usr/local/bin/cutting-entrypoint
RUN playwright install --with-deps chromium \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && chmod -R a+rX "$PLAYWRIGHT_BROWSERS_PATH" \
    && rm -rf /var/lib/apt/lists/*
COPY --chown=app:app backend ./backend
COPY --chown=app:app scripts/check_welding.py ./scripts/check_welding.py
COPY --from=frontend-builder --chown=app:app /build/frontend/dist ./frontend/dist
COPY --from=frontend-builder --chown=app:app /build/vendor ./vendor

RUN mkdir -p /app/data /app/reports /app/logs \
    && chown -R app:app /app/data /app/reports /app/logs

USER app

EXPOSE 8000
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).read()"]

ENTRYPOINT ["/usr/local/bin/cutting-entrypoint"]

CMD ["python", "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
