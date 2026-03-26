# Task 2 (인문논술) 에디터 구현 + Task 분기 로직 개선

> **날짜**: 2026-03-26
> **영향 파일**: `render_service.py`, `editor.html`, `base.html`

---

## 1. 배경

기존 에디터의 Task 분기 로직은 `add_info`에 `"논제"` 키가 있으면 Task 3으로 판단하고, 없으면 Task 1(교재)으로 처리했다.
Task 2(인문논술)도 `add_info.논제`를 가지고 있어 **Task 2와 Task 3을 구분할 수 없는 문제**가 존재했다.

| Task | 데이터 | 논제 구조 |
|---|---|---|
| Task 1 (교재) | `add_info.book_meta`, `add_info.문제`, `add_info.풀이` | `논제` 키 없음 |
| Task 2 (인문논술) | `add_info.논제.회차/출처/글자수/제목/본문` | `논제.문항` 없음 (스칼라 필드만) |
| Task 3 (수리논술) | `add_info.논제.문항.질문[{번호,본문,배점}]` | `논제.문항` 존재 (중첩 리스트) |

---

## 2. Task 분기 로직 개선

### 2.1 `detect_task_type()` 함수 추가 (`render_service.py`)

```python
def detect_task_type(add_info: dict) -> str:
    if "논제" not in add_info:
        return "task1"
    if "문항" in add_info["논제"]:
        return "task3"
    return "task2"
```

### 2.2 `render_item_card()` 분기 변경

**변경 전:**
```python
add_info = item.get("add_info", {}) or {}
if isinstance(add_info, dict) and "논제" in add_info:
    return render_task3_card(...)
# else: Task 1 렌더링
```

**변경 후:**
```python
add_info = item.get("add_info", {}) or {}
task_type = detect_task_type(add_info)
if task_type == "task3":
    return render_task3_card(...)
if task_type == "task2":
    return render_task2_card(...)
# else: Task 1 렌더링
```

---

## 3. Task 2 데이터 모델

### 3.1 `add_info` 구조

```json
{
  "pairIDX": "000000400440251",
  "논제": {
    "회차": "5월 2주차 인문 기초",
    "출처": "2023학년도 숭실대학교 모의논술고사 인문계",
    "글자수": "100자 내외",
    "제목": "다음 제시문에 드러난 문제를 요약하시오.",
    "본문": "몇몇의 미국 사람을 만났는데 ... (긴 텍스트)"
  },
  "논제분석": {
    "해설": "1. 논제 해설\n이번 논제는 ... <tag_P001_01> ... (태그 참조 포함)",
    "예시답안": "제시문에는 모든 미국인을 동일시하거나 ..."
  },
  "학생답안": "인간이 개별적으로 존재하는 것이 아닌 ...",
  "교사첨삭": {
    "총평가": [
      {"항목": "내 답안의 우수한 점", "유형": "positive", "내용": "99자로 주어진 분량을 충족하였습니다."},
      {"항목": "내 답안에서 보완해야 할 점", "유형": "negative", "내용": "분량을 고려하여 ..."}
    ],
    "세부평가": [
      {"항목": "답안 총평", "기준": ["A","B","C","D","E"], "결과": "B", "원본기준": ["A","B","C","D","E"]},
      {"항목": "독해력", "기준": ["A","B","C"], "결과": "B", "원본기준": ["상","중","하"]}
    ],
    "세부첨삭": [
      {"원본": "①*인간이 ... 행위*는 ... ②*집단에 ...*", "유형": ["기타"], "내용": "① 제시문에 ..."}
    ]
  },
  "source_file": "original/EHE_0034/EHE_0034_001.pdf"
}
```

### 3.2 Task 3과의 주요 차이

| 항목 | Task 2 (인문) | Task 3 (수리) |
|---|---|---|
| **논제** | 스칼라 5개 (회차,출처,글자수,제목,본문) | 문항.질문 리스트 구조 |
| **논제분석** | 해설 + 예시답안 (텍스트) | 예시답안.문항_질문 + 평가기준.문항_질문 (리스트) |
| **학생답안** | **단일 문자열** | 답안문 + 문항_질문 (리스트) |
| **교사첨삭** | 총평가 + 세부평가 + 세부첨삭 (**3중 구조**) | 평가 + 세부첨삭 (2중 구조) |

---

## 4. Task 2 에디터 UI 설계

### 4.1 섹션 구성 (5개)

```
📋 논제 (인디고) — 회차, 출처, 글자수, 제목, 본문
🔍 논제분석 (틸) — 해설, 예시답안
✍️ 학생답안 (오렌지) — 단일 텍스트
📝 교사첨삭 (핑크)
  ├─ 📊 총평가 (퍼플 카드) — [{항목, 유형, 내용}]
  ├─ 📈 세부평가 (틸 카드) — [{항목, 기준, 결과, 원본기준}]
  └─ ✍️ 세부첨삭 (앰버 카드) — [{원본, 유형, 내용}]
📁 출처정보 — pairIDX, source_file 등
```

### 4.2 리스트 카드 스키마 (`TASK2_LIST_SCHEMAS`)

| 경로 | 필드 | 테마 | 헤더 배지 |
|---|---|---|---|
| `교사첨삭.총평가` | 항목, 유형, 내용 | purple | `sentiment-badge` (positive/negative) |
| `교사첨삭.세부평가` | 항목, 기준, 결과, 원본기준 | teal | `eval-grade` (A/B/C/D/E 원형 배지) |
| `교사첨삭.세부첨삭` | 원본, 유형, 내용 | amber | — |

### 4.3 신규 CSS 요소

| 클래스 | 용도 |
|---|---|
| `.eval-grade` / `.eval-grade.A~E` | 세부평가 등급을 원형 배지로 시각화 |
| `.theme-teal` | 세부평가 카드 테마 (틸 계열) |

---

## 5. 구현 상세

### 5.1 백엔드 (`render_service.py`)

| 항목 | 설명 |
|---|---|
| `detect_task_type()` | `add_info` 구조로 task1/task2/task3 판별 |
| `TASK2_LIST_SCHEMAS` | 교사첨삭 하위 3개 리스트 스키마 정의 |
| `TASK2_FIELD_STYLES` | 세부첨삭 원본/내용 하이라이트 스타일 |
| `render_task2_card()` | 5개 섹션 HTML 카드 렌더링 함수 |
| `_render_list_items()` | `field_styles` 파라미터 추가로 Task 2/3 양쪽 지원 |

### 5.2 프론트엔드 (`editor.html`)

| 항목 | 설명 |
|---|---|
| `TASK2_LIST_SCHEMAS` | JS 리스트 스키마 (addListItem, _rebuildListFromDOM에서 참조) |
| `addListItem()` | `TASK3_LIST_SCHEMAS \|\| TASK2_LIST_SCHEMAS` 폴백 참조 |
| `_rebuildListFromDOM()` | 동일한 폴백 참조 적용 |

### 5.3 `_render_list_items()` 배지 분기 확장

```python
# 기존 (Task 3)
if "평가유형" in fields:        # → eval_type_badge + sentiment_badge
elif "첨삭본문이미지" in fields: # → detail_edit_type

# 추가 (Task 2)
elif "기준" in fields and "결과" in fields:  # → eval-grade 배지 (세부평가)
elif "항목" in fields and "유형" in fields:  # → sentiment-badge (총평가)
```

---

## 6. 수정 파일 요약

| 파일 | 변경 내용 | 변경 규모 |
|---|---|---|
| `app/services/render_service.py` | `detect_task_type()` + `TASK2_LIST_SCHEMAS` + `TASK2_FIELD_STYLES` + `render_task2_card()` + `_render_list_items()` 파라미터 확장 + 배지 분기 + 분기 로직 수정 | +270줄 (수정 포함) |
| `app/templates/editor.html` | `TASK2_LIST_SCHEMAS` JS 상수 + `addListItem`/`_rebuildListFromDOM` 스키마 폴백 | +7줄 (수정 포함) |
| `app/templates/base.html` | `.eval-grade` CSS + `.theme-teal` CSS | +17줄 |
| **합계** | | **+294줄** |

---

## 7. 검증 결과

### 7.1 Task 분기 테스트

| 입력 | 예상 | 결과 |
|---|---|---|
| `add_info = {논제: {회차, 출처, 글자수, 제목, 본문}}` | task2 | PASS |
| `add_info = {논제: {회차, 출처, 문항: {질문: [...]}}}` | task3 | PASS |
| `add_info = {book_meta, 문제, 풀이}` | task1 | PASS |
| `add_info = {}` | task1 | PASS |

### 7.2 Task 2 렌더링 검증 (EHE_0034_001)

- 5개 섹션 (논제, 논제분석, 학생답안, 교사첨삭, 출처정보) 정상 렌더링
- 3개 리스트 경로 (`총평가`, `세부평가`, `세부첨삭`) data-list-path 정상 생성
- 3개 테마 (`theme-purple`, `theme-teal`, `theme-amber`) 적용 확인
- `eval-grade` + `sentiment-badge` 배지 정상 렌더링
- **24/24 검증 항목 통과**

### 7.3 회귀 테스트

| Task | 검증 항목 | 결과 |
|---|---|---|
| Task 3 (EHE_0184) | 9개 항목 (섹션, 리스트 경로, Task 2 미혼입) | 전체 PASS |
| Task 1 (EPT_1021) | 2개 항목 (논제 없음, 문제 섹션 존재) | 전체 PASS |

---

## 8. 아키텍처 참고

### Task 분기 렌더링 흐름 (개선 후)

```
render_item_card(idx, item, comparison, gcs_image_base_url)
  ├── detect_task_type(add_info)
  │     ├── "논제" 없음 → "task1"
  │     ├── "논제.문항" 있음 → "task3"
  │     └── "논제.문항" 없음 → "task2"
  │
  ├── task3 → render_task3_card()
  │     ├── 논제 (문항.질문 리스트)
  │     ├── 논제분석 (예시답안.문항_질문, 평가기준.문항_질문)
  │     ├── 학생답안 (문항_질문 리스트)
  │     ├── 교사첨삭 (평가, 세부첨삭)
  │     └── 출처정보
  │
  ├── task2 → render_task2_card()
  │     ├── 논제 (회차, 출처, 글자수, 제목, 본문)
  │     ├── 논제분석 (해설, 예시답안)
  │     ├── 학생답안 (단일 문자열)
  │     ├── 교사첨삭 (총평가, 세부평가, 세부첨삭)
  │     └── 출처정보
  │
  └── task1 → 기존 렌더링 로직
        ├── content 섹션
        ├── add_info (book_meta, unit_meta, 문제, 풀이)
        └── source_file
```
