FROM python:3.10-slim

# Install uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── 의존성 캐시 레이어 ──
COPY pyproject.toml README.md ./
COPY app/__init__.py app/__init__.py
RUN uv pip install --system .

# ── 앱 코드 복사 ──
COPY . .

# Create data/log directories
RUN mkdir -p data/backups data/snapshots data/audit logs

EXPOSE 8000

# 운영: gunicorn.conf.py 기반 실행 (워커 수 등 환경 변수로 제어)
# 로컬 Docker: docker-compose.yml에서 command로 uvicorn --reload 덮어씀
CMD ["gunicorn", "app.main:app", "-c", "gunicorn.conf.py"]
