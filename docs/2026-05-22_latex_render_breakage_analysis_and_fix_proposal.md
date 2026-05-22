# LaTeX 수식 깨짐 분석 및 수정 제안서 (BUG-51)

- 작성일: 2026-05-22
- 대상: `EditerJsonlData`
- 이슈: content / 해설 등 모든 문자열 필드에서 LaTeX 수식이 특정 문자열(예: `0<x<...`) 포함 시 깨짐
- 브랜치: `bugfix/BUG-51-latex-html-safe-restore` (develop 분기)
- 상태: **적용 완료** — 본 문서 §8 참고

---

## 1) 재현 케이스

아래 문자열이 포함된 content/해설에서 렌더링 깨짐이 발생:

```text
${f}^{\prime}(x)={\begin{cases}−\pi{e}^{\cos\pi x}\sin\pi x{\left(0<x<\frac{1}{2}또는\frac{3}{2}<x<2)\right.}\\\pi{e}^{−\cos\pi x}\sin\pi x{\left(\frac{1}{2}<x<\frac{3}{2}\right)}\end{cases}}$이고 함수 $f (x)$는 $x = \frac{1}{2}, x = \frac{3}{2}$에서 미분가능하지 않다.
```

핵심 트리거: **수식 내부의 부등호 `<`** (예: `0<x<`, `<\frac`)

---

## 2) 원인 분석

### 2.1 수식 placeholder 복원 시 raw HTML 주입

- `app/services/render/base.py`
  - `protect_math_expressions()`가 `$...$` 구간을 placeholder로 치환
  - `escape_html()` 수행
  - `restore_math_expressions()`가 **원본 수식 문자열을 그대로** 복원

→ 복원된 HTML에 `<x`, `<\frac` 같은 토큰이 raw로 들어가 브라우저 HTML 파서가 태그 시작으로 오해석 → DOM 깨짐 → KaTeX는 이미 깨진 DOM을 받음

### 2.2 content 처리 경로에서 HTML 문자열 조립

- `app/services/render/components.py::process_content_with_tags()`
  - 최종 결과를 HTML 문자열로 합성하며 placeholder 복원 시 §2.1 문제 발현

### 2.3 과도하게 넓은 태그 분리 정규식

- 기존: `tag_pattern = r"(<[^>]+>)"`
- 메타 태그(`<tag_xxx>`) 처리가 목적이지만 일반 텍스트/수식의 `<...>`까지 잘못 매칭할 위험
- Phase 16의 `resolve_tags_in_html()`은 이미 `r"<(tag_[A-Za-z0-9_]+)>"`로 좁혀 사용 중 → **정합성 측면에서도 통일 필요**

### 2.4 사각지대: `render_meta_inline`의 `<table>` HTML 분기

- `components.py::render_meta_inline()`
  - `meta_type == "table"` 이고 `text_content`가 이미 `<table>...</table>` 문자열인 경우 **`resolve_tags_in_html()` 만 호출**되고 `protect_math_expressions` 경로를 거치지 않음
- 표 셀 내부에 `$0<x<1$` 같은 수식이 있으면 동일한 깨짐 발생
- `restore_math_expressions` 한 곳만 패치해서는 이 경로를 덮을 수 없음 → 별도 처리 필요

### 2.5 영향 범위 — 모든 문자열 필드

수식 깨짐은 `content_meta.table.text` 에만 국한되지 않고 **JSONL의 모든 문자열 필드**에서 발생할 수 있습니다. 다행히 렌더 패키지의 모든 문자열 출력 경로는 단 2개 함수로 수렴합니다.

- `format_text_with_newlines()` — 줄바꿈 + 수식 보호
- `process_content_with_tags()` — 태그 치환 + 수식 보호

두 함수 모두 **공통으로 `restore_math_expressions`를 호출**하므로 해당 함수 한 곳만 패치하면 아래 모든 필드가 일괄 보호됩니다.

| TASK | 자동 보호되는 필드 |
|---|---|
| TASK1 | `content` (dict/str/list), `add_info.문제.{선택지·보기·매칭항목·부가정보·...}`, `add_info.풀이.{정답·해설·...}` |
| TASK2 | `add_info.논제.{회차·출처·글자수·제목·본문}`, `add_info.논제분석.{해설·예시답안}`, `add_info.학생답안`, `add_info.교사첨삭.{총평가·세부평가·세부첨삭}[*].*` |
| TASK3 | `add_info.논제.{회차·출처·제시문·문항.지문}`, `add_info.논제.문항.질문[*].*`, `add_info.논제분석.해설`, `add_info.학생답안.*`, `add_info.교사첨삭.{평가·세부첨삭}[*].*` |
| 공통 | `content_meta`의 image/chart/기타 분기 모든 텍스트, 매칭 테이블 셀, 보기 항목, 모든 list-item-card 필드 |

→ §2.4 예외 1곳(`<table>` HTML)만 별도 처리하면 100% 커버됩니다.

---

## 3) 결론

KaTeX 자체 문제가 아니라 **KaTeX 실행 전 HTML 파싱 단계에서 `<`가 태그로 해석되는 것이 직접 원인**입니다.

---

## 4) 수정 제안

### 제안 A (즉시 적용) — 수식 복원 시 HTML-safe 처리

- placeholder 복원 시 원문을 그대로 넣지 말고 `<`, `>`, `&` 등을 entity로 처리해 HTML 파서가 태그로 인식하지 못하게 함
- KaTeX `auto-render`는 **텍스트 노드**의 `$...$`를 스캔하므로 entity는 브라우저 파싱 단계에서 다시 `<` 문자로 디코딩되어 KaTeX 입력에는 영향 없음
- 백슬래시(`\\`)는 escape 대상이 아니므로 TeX 명령어(`\frac`, `\begin{cases}` 등) 그대로 보존
- 적용 위치: `app/services/render/base.py::restore_math_expressions()`

### 제안 B (병행) — 태그 분리 정규식 축소

- 기존: `(<[^>]+>)` (너무 넓음)
- 권장: `(<tag_[A-Za-z0-9_]+>)`
- Phase 16의 `resolve_tags_in_html()` 정규식과 통일되어 정합성 확보
- 적용 위치: `app/services/render/components.py::process_content_with_tags()`

### 제안 C (보완) — 수식 보호 delimiter 4종 확장

- 기존: `$...$` 만 보호
- 권장: `\[...\]`, `\(...\)`, `$$...$$`, `$...$` (긴 delimiter 우선)
- **순서 함정 주의**: `$$x$$`가 `$` + `$x$` + `$`로 분해되지 않도록 반드시 `$$...$$`를 `$...$` 보다 먼저 매칭
- multi-line 수식을 위해 `re.DOTALL` 플래그 적용 (`$...$` 인라인은 제외)
- 적용 위치: `app/services/render/base.py::protect_math_expressions()`

### 제안 D (필수, 신규) — 예외 경로(`<table>` HTML) 보호

- `render_meta_inline`의 `meta_type == "table"` + 이미 HTML 형태인 분기에서도 `<` 부등호 entity화 필요
- 신규 헬퍼 `escape_math_in_html()`을 추가하여 이미 HTML인 텍스트 안의 수식만 안전 변환
- `resolve_tags_in_html()` 결과에 후처리로 적용
- 적용 위치: `app/services/render/base.py::escape_math_in_html()` (신규), `components.py::render_meta_inline()`

---

## 5) 검증 시나리오 (모두 PASS)

| # | 케이스 | 입력 예시 | 기대 결과 | 결과 |
|:-:|---|---|---|:-:|
| 1 | 기본 부등호 | `$0<x<1$` | 수식 정상 표시 | ✅ `$0&lt;x&lt;1$` |
| 2 | 복합 cases 수식 | `\begin{cases}...0<x<\frac{1}{2}...\end{cases}` | 전체 블록 깨짐 없음 | ✅ 내부 `<` 전부 entity화 |
| 3 | 메타 태그 + 수식 혼합 | `설명 <tag_img_1> 그리고 $a<b$` | 태그는 이미지로, 수식은 entity화 | ✅ |
| 4 | 일반 텍스트의 `<` | `문자열 0<x<y` | HTML 깨짐 없이 텍스트 표시 | ✅ `0&lt;x&lt;y` |
| 5 | 4종 delimiter 혼재 | `$$..$$`, `\(..\)`, `\[..\]`, `$..$` | 모든 delimiter 내부 `<` entity화 | ✅ |
| 6 | `<table>` HTML 내부 수식 | `<table><tr><td>$0<x<1$</td></tr></table>` | 표 구조 보존 + 수식 entity화 | ✅ |
| 7 | 회귀 (수식 없는 텍스트) | `그냥 문장\n두 번째 줄` | 기존 줄바꿈 심볼 정상 | ✅ |

검증 스크립트: 수정 직후 `python -c` 인라인 실행으로 7개 케이스 전수 통과 확인.

---

## 6) 적용 결과 (커밋 단위)

| 순서 | 파일 | 변경 |
|:-:|---|---|
| 1 | `app/services/render/base.py` | `restore_math_expressions`에 `escape_html` 적용 + `protect_math_expressions` 4종 delimiter 확장 + `escape_math_in_html` 신규 |
| 2 | `app/services/render/components.py` | `META_TAG_PATTERN` 상수화 + `process_content_with_tags`의 tag 분리 정규식 축소 + `render_meta_inline` table 분기에 `escape_math_in_html` 후처리 |

→ §2.5 표의 모든 필드 + §2.4 예외 경로가 한 번의 PR로 100% 커버됩니다.

---

## 7) 예상 영향

### 긍정 영향
- `<`/`>` 포함 수식 깨짐 해소 (TASK1/2/3 전 필드)
- HTML 파싱 안정성 향상 (일반 텍스트의 우발적 `<` 도 안전)
- 4종 delimiter 모두 보호 → KaTeX 데이터 호환성 확장
- `resolve_tags_in_html`과 `process_content_with_tags`의 메타 태그 정규식 통일

### 확인 필요 / Side-effect
- `data-original` / `data-raw-value` 편집 라운드트립: 영향 없음 (원시값 별도 저장 경로)
- Diff 뷰 (Phase 18 BUG-42): 영향 없음 (원시값 비교)
- 기존 `<tag_xxx>` 메타 렌더: 동작 유지 ✅
- 표 셀 내부 `<` 토큰: 표 구조(`<table>`/`<tr>`/`<td>`)는 보존하고 수식 안의 `<`만 entity화 ✅
- 정규식 우선순위: `$$...$$`가 `$...$`보다 먼저 매칭되도록 코드에 보장됨

### 잔여 한계
- `protect_math_expressions`의 인라인 `$...$`는 여전히 단일 라인만 지원 (multi-line 인라인 수식이 운영 데이터에 존재할 가능성은 낮음)
- 텍스트의 단일 `$` 문자(예: `$100`)는 다음 `$`까지 한 덩어리로 잘못 잡힐 수 있음 (기존 동작 유지) — 별도 PR로 다룰 사항

---

## 8) BUG 트래킹

- BUG 번호: **BUG-51**
- 분류: 렌더링 안정성 / HTML 파싱
- 영향 받은 사용자: 수식 포함 데이터를 다루는 모든 편집자
- 관련 기존 BUG: BUG-30 (테이블 내 태그 이미지 치환), BUG-29 (LaTeX 백슬래시 이스케이프)
- 핵심 교훈
  - 외부 라이브러리(KaTeX)에 데이터를 넘기기 전에 **HTML 파서가 먼저 데이터를 본다**는 점을 항상 의식할 것
  - placeholder 패턴은 복원 시점에도 **데이터의 안전성**을 책임져야 함
  - 사각지대 점검: 동일한 데이터가 흘러가는 모든 경로(특히 이미 HTML인 input)를 단위 함수 호출 기반으로 추적
