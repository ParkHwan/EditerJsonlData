# Task 3 (수리논술) 에디터 구현 + GCS 동기화 캐시 버그 수정

> **날짜**: 2026-03-26
> **브랜치**: `feature/editor_to_task3`
> **영향 파일**: `render_service.py`, `editor.html`, `base.html`, `file_service.py`, `gcs_edit_service.py`, `gcs.py`

---

## 1. Task 3 에디터 구현 (Phase 15)

### 1.1 배경

기존 에디터는 Task 1(교재)과 Task 2(인문논술)의 JSONL 구조를 처리하도록 설계되었다.
Task 3(수리논술)은 `add_info` 구조가 완전히 다르며, 5개 주요 섹션으로 구성된 계층적 데이터를 가진다.

| 섹션 | 포함 필드 | 중첩 리스트 |
|---|---|---|
| 논제 | 논제문, 논제유형, 문항 | 문항.질문 (`[{번호, 본문, 배점}]`) |
| 논제분석 | 예시답안, 평가기준 | 예시답안.문항_질문, 평가기준.문항_질문 |
| 학생답안 | 답안문, 문항질문 | 문항질문 (`[{번호, 답안}]`) |
| 교사첨삭 | 평가, 세부첨삭 | 평가 (`[{항목, 점수, 코멘트}]`), 세부첨삭 (`[{원본, 내용, 타입}]`) |
| 출처정보 | 학교, 과목, 연도 등 | — |

### 1.2 데이터 모델 참조

- Pydantic 모델: `cw_de_process/factory/KtTask3JsonStructure.py`
- 샘플 데이터: `data/originjsonl/EHE_0184_*.jsonl`

### 1.3 UI 설계

#### 섹션별 색상 테마

| 섹션 | 헤더 배경 | 테두리 색 |
|---|---|---|
| 논제 | `#e8eaf6` (인디고) | `#3f51b5` |
| 논제분석 | `#e0f2f1` (틸) | `#009688` |
| 학생답안 | `#fff3e0` (오렌지) | `#ff9800` |
| 교사첨삭 | `#fce4ec` (핑크) | `#e91e63` |
| 출처정보 | `#f3e5f5` (퍼플) | `#9c27b0` |

#### 리스트 아이템 카드 (`list-item-card`)

딕셔너리 형태의 리스트 아이템을 개별 키-값 행으로 분리 표시하여 세밀한 편집을 지원한다.

- 각 아이템은 인덱스 번호 헤더와 편집 가능한 필드 테이블로 구성
- 편집 모드에서 아이템 추가(+), 삭제(×), 이동(▲▼) 버튼 표시
- 필드별 하이라이트 스타일 지원 (세부첨삭의 원본=노랑, 내용=파랑)

#### pairIDX 사이드바 표시

- Task 3 데이터에는 `add_info.pairIDX` 필드가 존재
- 사이드바에 pairIDX 마지막 6자리를 표시
- 사이드바 검색에서 pairIDX 기반 필터링 지원

---

## 2. 구현 상세

### 2.1 CSS 추가 (`base.html`)

약 120줄의 Task 3 전용 CSS 추가:

- `.section-block`, `.section-header`: 섹션 블록 구조
- `.section-논제`, `.section-논제분석` 등: 섹션별 테마 색상
- `.list-item-card`, `.list-item-header`, `.list-item-idx`: 리스트 카드 컴포넌트
- `.theme-blue`, `.theme-teal` 등: 리스트 카드 색상 테마
- `.field-highlight-yellow`, `.field-highlight-blue`: 세부첨삭 필드 하이라이트
- `.eval-type-badge`, `.sentiment-badge`: 평가/감정 배지
- `.btn-list-add`, `.btn-list-delete`, `.btn-list-move`: 리스트 조작 버튼
- `.pair-idx-badge`: pairIDX 배지

### 2.2 서버사이드 렌더링 (`render_service.py`)

#### 상수 정의

```python
TASK3_LIST_SCHEMAS: dict[str, dict[str, Any]] = {
    "논제.문항.질문": {"fields": ["번호", "본문", "배점"], "theme": "blue", "label": "질문"},
    "논제분석.예시답안.문항_질문": {"fields": ["번호", "본문"], "theme": "teal", "label": "문항_질문"},
    "논제분석.평가기준.문항_질문": {"fields": ["번호", "본문"], "theme": "teal", "label": "문항_질문"},
    "학생답안.문항질문": {"fields": ["번호", "답안"], "theme": "orange", "label": "문항질문"},
    "교사첨삭.평가": {"fields": ["항목", "점수", "코멘트"], "theme": "pink", "label": "평가"},
    "교사첨삭.세부첨삭": {"fields": ["원본", "내용", "타입"], "theme": "pink", "label": "세부첨삭"},
}

TASK3_FIELD_STYLES: dict[str, str] = {
    "교사첨삭.세부첨삭.원본": "field-highlight-yellow",
    "교사첨삭.세부첨삭.내용": "field-highlight-blue",
}
```

#### 함수

| 함수 | 역할 |
|---|---|
| `_render_list_items()` | 리스트 아이템을 카드 형태로 렌더링 (스키마 기반) |
| `render_task3_card()` | Task 3 전체 카드 렌더링 (5개 섹션 + pairIDX 배지) |
| `render_item_card()` (수정) | `add_info`에 "논제" 키 존재 시 `render_task3_card()`로 분기 |

### 2.3 클라이언트사이드 편집 (`editor.html`)

#### JavaScript 상수/변수

```javascript
const modifiedLists = new Set();
const TASK3_LIST_SCHEMAS = {
    "add_info.논제.문항.질문": { fields: ["번호", "본문", "배점"], label: "질문" },
    // ... (서버 스키마와 동일, add_info. prefix 포함)
};
```

#### 수정된 함수

| 함수 | 변경 내용 |
|---|---|
| `setNestedValue()` | 숫자 경로 세그먼트 시 배열(`[]`) 생성 (기존: 항상 객체) |
| `deepMerge()` | 배열 재귀 병합 지원 (인덱스 기반, sparse 배열 안전 처리) |
| `enterInlineEdit()` | Task 3 리스트 조작 버튼 표시, `modifiedLists` 초기화 |
| `collectInlineChanges()` | `modifiedLists` 순회 → `_rebuildListFromDOM()` 호출 → 변경사항 반영 |
| `cancelEdit()` | 리스트 조작 버튼 숨김, `modifiedLists` 비어있지 않으면 카드 재렌더링 |
| `exitInlineEdit()` | `modifiedLists` 초기화, 리스트 조작 버튼 숨김 |
| `filterSidebar()` | pairIdx 기반 검색 지원 추가 |

#### 신규 함수

| 함수 | 역할 |
|---|---|
| `toggleSection(header)` | Task 3 섹션 접기/펼치기 |
| `addListItem(listPath)` | 빈 리스트 아이템 카드 추가 (스키마 기반 필드 생성) |
| `deleteListItem(listPath, index, btnEl)` | 리스트 아이템 삭제 표시 (`pending-delete` 클래스) |
| `moveListItem(listPath, index, direction)` | 리스트 아이템 순서 변경 |
| `_renumberListItems(container)` | 아이템 인덱스 재번호 부여 |
| `_rebuildListFromDOM(card, listPath)` | DOM에서 리스트 재구성 (삭제/추가/이동 반영) |

### 2.4 사이드바 pairIDX 표시

| 파일 | 변경 |
|---|---|
| `file_service.py` `get_data_id_list()` | `add_info.pairIDX` 추출하여 반환 |
| `gcs_edit_service.py` `get_data_id_list()` | `add_info.pairIDX` 추출하여 반환 |
| `editor.html` 사이드바 템플릿 | `data-pair-idx` 속성 + 마지막 6자리 표시 |
| `editor.html` `filterSidebar()` | pairIdx 포함 검색 |

### 2.5 Task 1/2 하위호환

- `render_item_card()`에서 `add_info`에 "논제" 키가 없으면 기존 렌더링 로직 사용
- Task 1/2 데이터에 대한 단위 테스트 통과 확인

---

## 3. GCS 동기화 캐시 버그 수정 (BUG-28)

### 3.1 증상

Task 3의 "GCS 동기화" 버튼 클릭 시 `동기화 완료: 폴더 0개, 파일 0개`가 반복적으로 표시됨.
실제 GCS에는 `TASK3/20260317/` 폴더에 13개 JSONL 파일이 존재함.

### 3.2 원인

`gcs_sync` 엔드포인트가 DuckDB sync 레코드는 초기화하지만, **GCS 목록 Redis 캐시를 무효화하지 않았음**.

```
gcs_service.list_date_folders() → Redis 캐시 키: gcs_cache:folders:{task_id}
gcs_service.list_files()        → Redis 캐시 키: gcs_cache:files:{task_id}:{date_str}
```

캐시 TTL은 30분이며, 최초 접근 시 빈 결과(`[]`)가 캐시되면 이후 모든 동기화 시도에서 캐시된 빈 결과만 반환.

**재현 흐름**:
1. Task 3 browse 페이지 최초 접근 → GCS에 아직 파일 없음 → `[]` 캐시 (30분 TTL)
2. GCS에 파일 업로드
3. "GCS 동기화" 클릭 → DuckDB 레코드 삭제 → `list_date_folders()` 호출 → 캐시 `[]` 반환 → 0개
4. 페이지 새로고침 → DuckDB 비어있음 → `list_date_folders()` 호출 → 캐시 `[]` 반환 → 0개
5. 30분 TTL 만료 전까지 동일 상황 반복

### 3.3 수정

| 위치 | 변경 |
|---|---|
| `gcs.py` `gcs_sync()` | `gcs_service.invalidate_cache()` 호출 추가 (DuckDB 레코드 삭제 직후) |
| `gcs.py` `_background_folder_sync()` | `gcs_service.invalidate_cache()` 호출 추가 (GCS 조회 직전) |
| 로그 메시지 | `cache_keys=%d` 파라미터 추가하여 무효화된 캐시 키 수 추적 |

### 3.4 수정 후 확인

```
# 의도적으로 빈 캐시 설정 (테스트)
redis-cli SET gcs_cache:folders:task3 "[]" EX 1800

# 동기화 API 호출
POST /api/v1/gcs/sync?task=task3
→ {"status":"ok","task":"task3","folders_synced":1,"files_synced":0}

# 서버 로그
Sync record cleared: task=task3 rows=1 cache_keys=2 by kanjanggun
DuckDB sync: task=task3 date=20260317 total=13 new=0
Manual GCS sync completed: task=task3 folders=1 files=0
```

캐시 무효화 후 GCS에서 실시간 조회하여 `20260317` 폴더(13개 파일) 정상 반환 확인.

---

## 4. 수정 파일 요약

| 파일 | 변경 내용 | 변경 규모 |
|---|---|---|
| `app/services/render_service.py` | Task 3 렌더링 함수 + 상수 + 분기 로직 | +402줄 |
| `app/templates/editor.html` | Task 3 JS 함수 + 기존 함수 수정 + 사이드바 | +232줄 |
| `app/templates/base.html` | Task 3 전용 CSS | +164줄 |
| `app/services/file_service.py` | pairIDX 추출 | +5줄 |
| `app/services/gcs_edit_service.py` | pairIDX 추출 | +5줄 |
| `app/api/v1/endpoints/gcs.py` | GCS 캐시 무효화 추가 | +7줄 (수정) |
| **합계** | | **+806줄, -9줄** |

---

## 5. 아키텍처 참고

### Task 3 렌더링 분기 흐름

```
render_item_card(idx, item, comparison, gcs_image_base_url)
  ├── add_info에 "논제" 키 존재? → render_task3_card()
  │     ├── 5개 섹션 블록 렌더링
  │     ├── 리스트 필드 → _render_list_items() (TASK3_LIST_SCHEMAS 기반)
  │     └── 스칼라 필드 → info-table 행
  └── "논제" 키 없음 → 기존 Task 1/2 렌더링 로직
```

### GCS 동기화 캐시 흐름 (수정 후)

```
[GCS 동기화 버튼 클릭]
  → gcs_sync() 엔드포인트
  → metadata_service.clear_sync_record(task)     ← DuckDB 초기화
  → gcs_service.invalidate_cache()               ← Redis 캐시 무효화 (신규)
  → gcs_service.list_date_folders(task_id=task)   ← GCS 실시간 조회
  → for folder: list_files() → sync_files_from_gcs()
  → 결과 반환 + location.reload()
```
