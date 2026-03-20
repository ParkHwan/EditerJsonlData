FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── 의존성 캐시 레이어 ──
COPY pyproject.toml ./
COPY app/__init__.py app/__init__.py
RUN touch README.md && uv pip install --system . && rm README.md

# ── 앱 코드 복사 ──
COPY app/ app/
COPY static/ static/
COPY gunicorn.conf.py ./

# ── 런타임 디렉터리 ──
RUN mkdir -p data/backups data/snapshots data/audit logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:8000/api/v1/health || exit 1

CMD ["gunicorn", "app.main:app", "-c", "gunicorn.conf.py"]
