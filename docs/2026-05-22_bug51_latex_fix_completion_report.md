# BUG-51 LaTeX 수식 깨짐 — 작업 완료 보고서 & 향후 참고 가이드

- 작성일: 2026-05-22
- 작성자: 박환 + AI assist
- 대상: `EditerJsonlData`
- 분류: bugfix (사용자 체감 렌더링 결함)
- 브랜치: `bugfix/BUG-51-latex-html-safe-restore` (develop 분기)
- 커밋: `5a9fae3 fix: LaTeX 수식 내부 부등호로 인한 HTML 파싱 깨짐 수정 (BUG-51)`
- PR: <https://github.com/ParkHwan/EditerJsonlData/pull/21>
- 분석 원본: `docs/2026-05-22_latex_render_breakage_analysis_and_fix_proposal.md`

---

## 1) 작업 타임라인

| 단계 | 내용 |
|:-:|---|
| 1. 이슈 보고 | `${f}^{\prime}(x)={\begin{cases}...0<x<\frac{1}{2}...\}}$` 수식이 화면에서 깨짐 |
| 2. 분석 | `restore_math_expressions`가 placeholder를 raw 문자열로 복원 → 수식 내부 `<` 가 HTML 파서에 태그로 오인 |
| 3. 영향 범위 매핑 | content / 정답 / 해설 / 보기 / 매칭항목 / 부가정보 등 모든 문자열 필드가 동일 함수 경로를 거침을 확인 |
| 4. 사각지대 발견 | `render_meta_inline`의 `<table>` HTML 분기는 `protect/restore` 경로를 건너뜀 |
| 5. 제안 A~D 도출 | restore HTML-safe + tag_pattern 축소 + delimiter 4종 확장 + 예외 경로 보호 |
| 6. 패치 적용 | base.py + components.py 2개 파일 / 함수 4개 변경·신규 |
| 7. 검증 | 7개 시나리오 인라인 전수 PASS + ruff/pyright 린트 0건 + import 정합성 |
| 8. PR 생성 | `bugfix → develop` PR #21 |

---

## 2) 변경 요약 (재사용 가능한 패턴)

### 2.1 변경 파일

```
app/services/render/base.py         (+42 / -3)
app/services/render/components.py   (+12 / -3)
docs/2026-05-22_latex_render_breakage_analysis_and_fix_proposal.md (신규)
docs/2026-05-22_bug51_latex_fix_completion_report.md               (본 문서)
```

### 2.2 핵심 코드 변경 (요지)

| 함수 | 변경 |
|---|---|
| `base.py::restore_math_expressions` | placeholder 복원 시 `escape_html(original)` 적용 |
| `base.py::protect_math_expressions` | 4종 delimiter 보호 확장 (`\[..\]`, `\(..\)`, `$$..$$`, `$..$`, 긴 delimiter 우선 + `re.DOTALL`) |
| `base.py::escape_math_in_html` (신규) | 이미 HTML 문자열인 텍스트 안의 수식만 안전 entity화 |
| `components.py::META_TAG_PATTERN` (신규 상수) | `(<tag_[A-Za-z0-9_]+>)` 로 통일 |
| `components.py::process_content_with_tags` | 위 상수 사용으로 tag 분리 정규식 축소 |
| `components.py::render_meta_inline` (table 분기) | `resolve_tags_in_html` 결과에 `escape_math_in_html` 후처리 |

### 2.3 자동 보호되는 필드 (1줄 패치로 전부 커버)

| TASK | 보호되는 필드 |
|---|---|
| TASK1 | `content` (dict/str/list), `add_info.문제.{선택지·보기·매칭항목·부가정보·...}`, `add_info.풀이.{정답·해설·...}` |
| TASK2 | `add_info.논제.{회차·출처·글자수·제목·본문}`, `논제분석`, `학생답안`, `교사첨삭.{총평가·세부평가·세부첨삭}[*].*` |
| TASK3 | `add_info.논제.{회차·출처·제시문·문항.지문}`, `논제.문항.질문[*].*`, `논제분석.해설`, `학생답안.*`, `교사첨삭.{평가·세부첨삭}[*].*` |
| 공통 | `content_meta`의 image/chart/기타 분기 모든 텍스트, 매칭 테이블 셀, 보기 항목, 모든 list-item-card 필드, `<table>` HTML 내부 수식 |

---

## 3) 검증 결과

| # | 케이스 | 입력 | 기대 | 결과 |
|:-:|---|---|---|:-:|
| 1 | 기본 부등호 | `$0<x<1$` | `$0&lt;x&lt;1$` | PASS |
| 2 | 복합 cases | `\begin{cases}...0<x<\frac{1}{2}...\end{cases}` | 내부 `<` 전부 entity화 | PASS |
| 3 | 메타 태그 + 수식 | `설명 <tag_img_1> 그리고 $a<b$` | 태그→이미지, 수식 entity화 | PASS |
| 4 | 일반 텍스트 `<` | `문자열 0<x<y` | `0&lt;x&lt;y` | PASS |
| 5 | 4종 delimiter | `$$..$$`, `\(..\)`, `\[..\]`, `$..$` | 모두 entity화 | PASS |
| 6 | `<table>` HTML | `<table><tr><td>$0<x<1$</td></tr></table>` | 표 구조 보존 + 수식 entity화 | PASS |
| 7 | 회귀 | `그냥 문장\n두 번째 줄` | 기존 줄바꿈 심볼 정상 | PASS |

### 머지 후 운영 검수 체크리스트 (사람이 눈으로 확인할 항목)
- [ ] TASK1 EPT_* 데이터 중 LaTeX 포함 row 3건 시각 검수
- [ ] TASK2/3 논제분석.해설에 수식 포함된 row 시각 검수
- [ ] `content_meta.tag_*.type == "table"` + 표 내부 수식 포함 row 시각 검수
- [ ] 편집모드 진입 → 편집모드 종료 라운드트립 시 원본 값 보존 확인
- [ ] 브라우저 콘솔에 KaTeX 관련 에러 없음 확인

---

## 4) 향후 참고 가이드

### 4.1 "수식이 깨진다" 보고가 다시 들어왔을 때 진단 흐름

```
1) 어느 필드에서 깨지는가? (content / 해설 / 표 셀 / 보기 / ...)
   │
   ├── 모든 필드에서 깨짐 → base.py::restore_math_expressions / protect_math_expressions 의심
   │
   ├── 특정 필드에서만 깨짐 → 해당 필드 렌더 경로 추적
   │     → task1/2/3.py에서 어떤 함수를 호출하는지 확인
   │     → 그 함수가 process_content_with_tags / format_text_with_newlines 를 거치는지 확인
   │     → 거치지 않으면 사각지대 (BUG-51-D 케이스와 동일)
   │
   └── 표 내부에서만 깨짐 → render_meta_inline의 table 분기 확인 (escape_math_in_html 적용 여부)

2) 깨짐의 양상은?
   ├── DOM이 통째로 깨짐 → HTML 파싱 단계 문제 (escape 누락 의심)
   ├── 수식 일부만 깨짐 → KaTeX delimiter 인식 실패 (protect 정규식 의심)
   └── 중복 표시 → CSP/CSS 누락 (BUG-15 케이스, security_headers.py 확인)

3) 브라우저 DevTools 확인 포인트
   ├── Elements 탭에서 수식 직전 영역의 DOM 구조 확인
   ├── 텍스트 노드인지, 의도치 않은 태그가 끼어있는지
   └── Console에서 KaTeX 에러 메시지 (parse error, undefined command 등)
```

### 4.2 렌더 패키지에 신규 함수/필드 추가 시 체크리스트

새로운 텍스트 출력 함수를 만들거나, JSONL에 새 문자열 필드를 추가할 때:

- [ ] 출력이 **HTML 문자열을 직접 조립**하는가? → 수식 보호 필요
- [ ] `format_text_with_newlines()` 또는 `process_content_with_tags()` 중 하나를 거치는가?
  - **거친다** → 자동 보호됨, 추가 작업 불필요
  - **거치지 않는다** → 둘 중 적절한 것을 호출하거나, 이미 HTML이면 `escape_math_in_html()` 사용
- [ ] `data-field` 속성으로 인라인 편집 대상이 되는가? → 원시값 보존 경로(`data-original` / `data-raw-value`) 확인
- [ ] `<table>` 등 사전 HTML 구조 데이터를 출력하는가? → BUG-51-D 사각지대 케이스, `escape_math_in_html` 후처리 필수

### 4.3 일반화 교훈 — "외부 라이브러리는 항상 HTML 파서 다음"

KaTeX, MathJax, Mermaid, Prism 등 **DOM을 후처리하는 라이브러리**는 모두 동일한 원리에 노출됩니다.

- 브라우저는 **HTML 파싱 → DOM 구성 → 라이브러리 후처리** 순서로 동작
- 라이브러리 입력이 텍스트 노드라면 **HTML 파싱 단계에서 이미 데이터가 깨질 수 있음**
- placeholder 패턴(protect/restore)을 쓰더라도 **복원 시점의 HTML-safe 보장**이 책임

### 4.4 정규식 안전 패턴 — Delimiter 우선순위

다중 delimiter를 매칭할 때는 **더 긴 delimiter 우선 매칭**이 철칙입니다.

```python
# 잘못된 예 — $$x$$ 가 $ + $x$ + $ 로 분해됨
text = re.sub(r"\$(.+?)\$", _replace, text)
text = re.sub(r"\$\$(.+?)\$\$", _replace, text)

# 올바른 예 — 긴 것부터
text = re.sub(r"\$\$(.+?)\$\$", _replace, text)
text = re.sub(r"\$(.+?)\$", _replace, text)
```

### 4.5 patch 영향도가 큰 함수일수록 단위 함수 호출 그래프부터

이번 케이스의 핵심 통찰: **모든 문자열 렌더링이 2개 함수로 수렴**한다는 사실을 먼저 확인했기 때문에, 1줄 패치로 95% 케이스가 자동 해결되었습니다.

→ **렌더 패키지에서 작업할 땐 항상 `Grep` 으로 호출 그래프를 먼저 매핑**하기.

---

## 5) 이번에 보류한 항목 — 재평가 트리거 매트릭스

| 항목 | 출처 | 현재 우선순위 | 재평가 트리거 (이 신호가 보이면 즉시 작업) |
|---|---|:-:|---|
| onclick XSS (`'` escape 누락) | 전수 검토 #1 | 🟡 P2 | `'` 포함 키명 입력 → UI 멈춤 버그 리포트 / 외부 사용자에게 서비스 공개 |
| 테스트 코드 정합화 | 전수 검토 #2 | 🟢 P2 | 다음 큰 기능 추가 시작 직전 / 회귀 버그 1건이라도 자동 탐지 실패 시 |
| README 갱신 | 전수 검토 #3 | 🟢 P3 | 신규 인원 합류 예정 / 외부 협업 시작 |
| DEBUG 파싱 방어 | 전수 검토 #4 | ⏸️ P3 | staging 환경 추가 / `.env` 오타로 시작 실패 사고 발생 |
| RISK-01 Lock Race | 동시성 보고서 | ✅ 해결됨 | (재발 시 lock_service.py 회귀 확인) |
| RISK-02 ZIP OOM | 동시성 보고서 | 🟡 P2 | 메모리 피크가 maxmem 70% 초과 / OOM 1건 발생 |
| RISK-03 Redis allkeys-lru | 동시성 보고서 | 🟡 P2 | Redis 사용량이 maxmem 50% 초과 / 세션·Lock 갑작스러운 손실 보고 |
| RISK-04~06 이벤트 루프/락 | 동시성 보고서 | ⏸️ P3 | 동시 사용자 30+ / "편집 저장이 느려요" 피드백 / p95 응답 1s 초과 |

### "재평가 트리거" 사용 방법
- 위 신호가 보이면 해당 항목을 **즉시 P0~P1로 승급** 하고 다음 sprint에 끼워넣기
- 아무 신호도 없으면 분기별 1회만 점검 (이 표를 다시 읽기)

---

## 6) 핵심 교훈 (재발 방지용)

1. **KaTeX 등 외부 라이브러리 입력 전에 HTML 파서가 데이터를 먼저 본다**
   - placeholder 패턴은 복원 시점에도 HTML-safe 보장 필수
2. **렌더 함수 호출 그래프를 먼저 매핑**하면 작은 패치로 큰 영향을 낼 수 있다
   - `Grep`으로 `process_content_with_tags`, `format_text_with_newlines` 호출처를 모두 확인한 것이 결정적이었음
3. **사각지대(이미 HTML 형태로 들어오는 경로)는 별도 처리** 필요
   - 표/차트/이미지 캡션 등은 사전 HTML이 흔하므로 점검 우선순위
4. **정규식 패턴은 한 곳에서 정의하고 공유** (`META_TAG_PATTERN`)
   - 동일 의미의 패턴이 여러 곳에 흩어지면 BUG-50 같은 regression 재발
5. **delimiter 우선순위는 긴 것이 먼저** (`$$..$$` 가 `$..$` 보다 먼저)
6. **검증은 분석 단계에 정의한 시나리오를 코드로 실행**하여 PASS 증거 남기기
   - 운영 데이터 가시 검수만으로는 회귀 보장 어려움

---

## 7) 관련 BUG / 문서

- BUG-15 (2026-03-20): CSP 헤더 KaTeX CSS/폰트 차단 → 중복 표시
- BUG-29 (2026-03-26): LaTeX 편집모드 백슬래시 이중 이스케이프
- BUG-30 (2026-03-27): 테이블 text_content 내 `<tag_...>` 미치환 (이번 사각지대와 동일 위치)
- 본 BUG-51 (2026-05-22): 수식 내부 `<` HTML 파서 오해석 — **본 보고서**

### 참고 문서
- `docs/2026-05-22_latex_render_breakage_analysis_and_fix_proposal.md` — 분석/제안서 (본 보고서의 사전 분석 자료)
- `docs/2026-05-22_project_full_review_report.md` — 134개 파일 전수 검토 결과
- `docs/2026-04-02_concurrency_load_scenario_report.md` — 동시성 부하 분석 (RISK 14건)
- `docs/JSONL_SCHEMA.md` — TASK1/2/3 JSONL 스키마 명세
- Serena 메모리 `bug_tracking` — BUG-01~51 추적 이력
