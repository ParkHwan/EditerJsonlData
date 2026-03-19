# Phase 11: 이메일/비밀번호 인증 + 관리자 사용자 관리

**작성일**: 2026-03-14  
**작성자**: AI Assistant + 사용자 협업  
**대상 프로젝트**: EditerJsonlData  
**환경**: Docker Compose (macOS Darwin 25.3.0)

---

## 1. 개요

기존 ID/이름 직접 입력 방식의 인증을 **crowdworks.kr 도메인 이메일 + 비밀번호 기반 인증**으로 전환.
관리자가 사용자를 직접 등록하고, 비밀번호를 bcrypt 해싱하여 DuckDB에 저장하는 구조.

### 설계 배경

| 검토 방안 | 결과 |
|-----------|------|
| Google OAuth2 (GCP 인증 정보 필요) | GCP 콘솔 접근 권한 부재로 불가 |
| 이메일 + 비밀번호 (관리자 등록) | **채택** — 외부 서비스 의존 없음 |
| 이메일 + OTP (SMTP 필요) | SMTP 설정 필요, 과도 |

---

## 2. 인증 플로우

```
[관리자]
  사용자 관리 페이지 → 신규 사용자 등록
  → email@crowdworks.kr + 초기 비밀번호 입력
  → bcrypt 해싱 → DuckDB users 테이블 INSERT

[사용자]
  로그인 페이지 → 이메일 + 비밀번호 입력
  → @crowdworks.kr 도메인 검증 (클라이언트 + 서버)
  → DuckDB 사용자 조회 (이메일 기반)
  → is_active 확인 (비활성 계정 거부)
  → bcrypt 비밀번호 검증
  → Redis 세션 생성 (48h TTL) + 쿠키 설정
  → 파일 목록 페이지 리다이렉트
```

---

## 3. DuckDB 스키마 변경

### users 테이블 (Phase 11 확장)

| 컬럼 | 타입 | 설명 | Phase 11 추가 |
|------|------|------|:---:|
| user_id | VARCHAR PK | 이메일 로컬 파트 | |
| display_name | VARCHAR NOT NULL | 표시 이름 | |
| **email** | VARCHAR | crowdworks.kr 이메일 | ✅ |
| **password_hash** | VARCHAR | bcrypt 해시 | ✅ |
| **is_admin** | BOOLEAN DEFAULT false | 관리자 여부 | ✅ |
| **is_active** | BOOLEAN DEFAULT true | 활성/비활성 | ✅ |
| first_login_at | TIMESTAMPTZ | 최초 로그인 | |
| last_login_at | TIMESTAMPTZ | 마지막 로그인 | |
| login_count | INTEGER | 로그인 횟수 | |

### 마이그레이션 전략

기존 DB에 새 컬럼이 없으면 서버 시작 시 `ALTER TABLE ADD COLUMN`으로 자동 추가.
DuckDB는 `NOT NULL` 제약조건을 `ALTER`에서 지원하지 않으므로 `DEFAULT` 값만 설정.

```python
# duckdb_client.py → _migrate_users_table()
migrations = [
    ("email", "ALTER TABLE users ADD COLUMN email VARCHAR DEFAULT ''"),
    ("password_hash", "ALTER TABLE users ADD COLUMN password_hash VARCHAR DEFAULT ''"),
    ("is_admin", "ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT false"),
    ("is_active", "ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT true"),
]
```

---

## 4. API 엔드포인트

### 인증 (`/api/v1/auth/`)

| Method | Path | 설명 | 권한 |
|--------|------|------|------|
| POST | `/login` | 이메일+비밀번호 로그인 | 공개 |
| POST | `/logout` | 로그아웃 | 인증 |
| GET | `/me` | 현재 사용자 정보 | 인증 |

### 사용자 관리 (`/api/v1/auth/users/`)

| Method | Path | 설명 | 권한 |
|--------|------|------|------|
| POST | `/users/register` | 신규 사용자 등록 | 관리자 |
| GET | `/users` | 사용자 목록 조회 | 관리자 |
| PATCH | `/users/{user_id}/toggle` | 활성/비활성 토글 | 관리자 |
| PATCH | `/users/{user_id}/password` | 비밀번호 재설정 | 관리자 |
| DELETE | `/users/{user_id}` | 사용자 삭제 | 관리자 |

### 뷰 페이지

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/v1/view/login` | 로그인 페이지 |
| GET | `/api/v1/view/admin/users` | 관리자 사용자 관리 페이지 |

---

## 5. 보안

### 비밀번호

- **bcrypt** (cost factor 12) 단방향 해싱
- 평문 비밀번호는 어디에도 저장하지 않음
- `bcrypt.checkpw()`로 검증

### 도메인 제한

- 클라이언트: JavaScript에서 `@crowdworks.kr` 접미사 검증
- 서버: `AuthService.validate_email_domain()` 이중 검증
- 설정: `ALLOWED_EMAIL_DOMAIN` 환경 변수 (기본: `crowdworks.kr`)

### 세션

- Redis 기반 서버사이드 세션 (기존 유지)
- 48시간 TTL, HttpOnly 쿠키
- 세션 고정 공격 방지 (로그인 시 새 세션 ID)
- IP/User-Agent 변경 감지 (경고 로그)

### 관리자 권한

- `is_admin` 플래그로 세션에 저장
- `_require_admin()` 헬퍼로 관리자 전용 엔드포인트 보호
- 네비바에 관리자만 "사용자 관리" 메뉴 표시

---

## 6. 변경 파일 목록

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `pyproject.toml` | 수정 | `authlib` 제거, `bcrypt>=4.1.0` 추가 |
| `app/core/config.py` | 수정 | Google OAuth 설정 제거, `ALLOWED_EMAIL_DOMAIN` 추가 |
| `.env` / `.env.example` | 수정 | Google OAuth 변수 제거, `ALLOWED_EMAIL_DOMAIN` 추가 |
| `app/db/duckdb_client.py` | 수정 | users 스키마 확장 + `_migrate_users_table()` 자동 마이그레이션 |
| `app/services/auth_service.py` | 전면 수정 | Google OAuth 제거, bcrypt 해싱/검증, 도메인 검증 |
| `app/services/metadata_service.py` | 추가 | 사용자 CRUD 메서드 8개 (register, get_by_email, list, toggle, password, delete 등) |
| `app/api/v1/endpoints/auth.py` | 전면 수정 | 이메일/비밀번호 로그인 + 관리자 CRUD 엔드포인트 8개 |
| `app/api/v1/endpoints/files.py` | 수정 | 관리자 사용자 관리 뷰 페이지 추가 |
| `app/main.py` | 수정 | SessionMiddleware 제거, 최초 관리자 자동 생성 |
| `app/templates/login.html` | 전면 수정 | 이메일+비밀번호 입력 폼 |
| `app/templates/admin_users.html` | 신규 | 사용자 목록/등록/비밀번호 재설정/삭제 관리 UI |
| `app/templates/base.html` | 수정 | 관리자 "사용자 관리" 네비 메뉴 추가 |

---

## 7. 초기 관리자 계정

서버 최초 기동 시 관리자 계정이 없으면 자동 생성:

| 항목 | 값 |
|------|-----|
| 이메일 | `kanjanggun@crowdworks.kr` |
| 초기 비밀번호 | `admin1234` |
| user_id | `kanjanggun` |
| 권한 | 관리자 (is_admin=true) |

> 운영 환경에서는 초기 비밀번호를 즉시 변경할 것을 권장합니다.

---

## 8. 관리자 사용자 관리 UI

관리자 로그인 후 상단 네비바 → "사용자 관리" 클릭:

- **사용자 등록**: 이름 + @crowdworks.kr 이메일 + 초기 비밀번호 + 권한(일반/관리자)
- **사용자 목록**: 이름, 이메일, 권한, 상태, 로그인 횟수, 최근 로그인 일시
- **비밀번호 재설정**: 모달 팝업에서 새 비밀번호 입력
- **활성/비활성 토글**: 비활성화 시 해당 사용자 로그인 불가
- **사용자 삭제**: 확인 다이얼로그 후 삭제 (자기 자신 삭제 불가)

---

## 9. 검증 결과

| 테스트 케이스 | 결과 |
|-------------|------|
| 정상 로그인 (`kanjanggun@crowdworks.kr` + `admin1234`) | 성공 (세션 + 쿠키) |
| 잘못된 도메인 (`hacker@gmail.com`) | 거부 (403) |
| 잘못된 비밀번호 | 거부 (401) |
| 미등록 이메일 | 거부 (401) |
| 관리자 사용자 등록 | 성공 (DuckDB INSERT) |
| 관리자 사용자 목록 조회 | 성공 (2명 표시) |
| `/me` API (is_admin 포함) | 정상 반환 |
