# ── EditerJsonlData Makefile (Phase 7) ──
# 로컬 개발과 운영 환경을 간편하게 제어

.PHONY: help dev dev-docker dev-down test prod prod-logs prod-down prod-restart prod-build ssl-self-signed

help: ## 사용 가능한 명령어 표시
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ────────────────────────────────
# 로컬 개발
# ────────────────────────────────

dev: ## uvicorn --reload 로컬 실행 (Redis 별도 필요)
	ENV_FILE=.env.local uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

dev-docker: ## Docker Compose 로컬 개발 환경 실행
	docker compose up -d --build

dev-down: ## Docker Compose 로컬 개발 환경 중지
	docker compose down

dev-logs: ## 로컬 Docker 로그 확인
	docker compose logs -f web

test: ## pytest 실행
	pytest -v

lint: ## pyright 타입 체크
	pyright app/

# ────────────────────────────────
# GCP 운영
# ────────────────────────────────

prod: ## 운영 환경 Docker Compose 실행
	docker compose -f docker-compose.prod.yml up -d --build

prod-logs: ## 운영 로그 확인 (web + nginx)
	docker compose -f docker-compose.prod.yml logs -f web nginx

prod-down: ## 운영 환경 중지
	docker compose -f docker-compose.prod.yml down

prod-restart: ## 운영 환경 재시작 (web만)
	docker compose -f docker-compose.prod.yml restart web nginx

prod-build: ## 운영 이미지만 빌드 (배포 전 확인)
	docker compose -f docker-compose.prod.yml build web

prod-redis-logs: ## 운영 Redis 로그 확인
	docker compose -f docker-compose.prod.yml logs -f redis-master redis-sentinel

# ────────────────────────────────
# 유틸리티
# ────────────────────────────────

ssl-self-signed: ## 자체 서명 SSL 인증서 생성 (테스트용)
	openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
		-keyout nginx/ssl/server.key \
		-out nginx/ssl/server.crt \
		-subj "/CN=localhost"
	@echo "SSL 인증서 생성 완료: nginx/ssl/server.{crt,key}"

clean: ## Docker 볼륨 및 캐시 정리
	docker compose down -v
	docker compose -f docker-compose.prod.yml down -v 2>/dev/null || true
	docker system prune -f
