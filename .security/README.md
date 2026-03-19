# GCS 인증 설정 (.security)

이 디렉터리에 GCP 서비스 계정 키 JSON 파일을 저장합니다.
`*.json` 파일은 `.gitignore`에 포함되어 Git에 추적되지 않습니다.

## 사용법

1. GCP 콘솔 → IAM & Admin → Service Accounts
2. 서비스 계정 생성 (또는 기존 계정 선택)
3. 키 → JSON 키 생성 → 다운로드
4. 이 디렉터리에 `gcs-credentials.json`으로 저장

```bash
cp ~/Downloads/your-service-account-key.json .security/gcs-credentials.json
```

## 필요 권한

- `roles/storage.objectUser` (읽기 + 쓰기)
- 대상 버킷: `de-download-service-storage`

## 인증 우선순위

1. `GCS_CREDENTIALS_PATH` 환경변수 (명시 지정)
2. `.security/gcs-credentials.json` (이 파일)
3. 호스트 `~/.config/gcloud/` (gcloud ADC 폴백)

## 주의

- 키 파일을 절대 Git에 커밋하지 마세요.
- Docker에서는 read-only로 마운트됩니다.
