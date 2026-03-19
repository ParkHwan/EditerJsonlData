"""Gunicorn 운영 설정 (Phase 7)

환경 변수로 워커 수, 타임아웃 등을 제어한다.
Docker Compose 운영 환경에서 사용:
    gunicorn app.main:app -c gunicorn.conf.py
"""

import multiprocessing
import os

# ── 서버 바인딩 ──
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8000")

# ── 워커 설정 ──
_workers_env = int(os.getenv("WORKERS", "0"))
workers = _workers_env if _workers_env > 0 else multiprocessing.cpu_count() * 2 + 1
worker_class = "uvicorn.workers.UvicornWorker"

# ── 타임아웃 ──
timeout = int(os.getenv("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

# ── 프로세스 관리 ──
preload_app = True
max_requests = 1000
max_requests_jitter = 50

# ── 로깅 ──
_log_level = os.getenv("LOG_LEVEL", "info").lower()
loglevel = _log_level
accesslog = os.getenv("GUNICORN_ACCESS_LOG", "-")
errorlog = os.getenv("GUNICORN_ERROR_LOG", "-")
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)sμs'

# ── 보안 ──
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190
