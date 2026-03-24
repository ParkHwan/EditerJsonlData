# 버그 수정 리포트 (2026-03-23)

> Phase 14 개발 및 배포 과정에서 발견된 버그 수정 기록

---

## BUG-16: Starlette 1.0.0 TemplateResponse TypeError

| 항목 | 내용 |
|---|---|
| **증상** | `GET /api/v1/view/login` 접속 시 `TypeError: unhashable type: 'dict'` 발생 |
| **원인** | Starlette 1.0.0에서 `TemplateResponse` API 시그니처가 변경됨. `request`가 첫 번째 위치 인자가 되고, context 딕셔너리에서 제외해야 함 |
| **영향** | 모든 HTML 뷰 페이지 (로그인, 파일목록, 에디터, GCS 브라우저) 접속 불가 |

### 에러 스택트레이스

```
File "starlette/templating.py", in __init__
    ...
TypeError: unhashable type: 'dict'
```

### 수정 내용

```python
# 이전 (Starlette <1.0)
templates.TemplateResponse(
    "template.html",
    {"request": request, "key": value, ...}
)

# 이후 (Starlette 1.0.0)
templates.TemplateResponse(
    request,
    "template.html",
    {"key": value, ...}  # request 제거
)
```

### 수정 범위

- `app/api/v1/endpoints/files.py` — 5개 호출
- `app/api/v1/endpoints/gcs.py` — 2개 호출

---

## BUG-17: DuckDB 직렬화 에러 (버전 비호환)

| 항목 | 내용 |
|---|---|
| **증상** | 로그인 시 `_duckdb.SerializationException: Serialization Error: Failed to deserialize: field id mismatch, expected: 100, got: 0` |
| **원인** | `data/editor.duckdb` 파일이 DuckDB 1.2.x로 생성되었으나, Docker 컨테이너에 설치된 DuckDB는 1.5.0. DB 파일 포맷이 버전 간 호환되지 않음 |
| **영향** | 로그인 불가 (DuckDB 사용자 조회 실패) |

### 수정 내용

```bash
# 1. 기존 DB 파일 백업
mv data/editor.duckdb data/editor.duckdb.bak.v1.2

# 2. 컨테이너 재시작 → DuckDB 1.5.0 포맷으로 자동 재생성
docker compose -f docker-compose.prod.yml restart web

# 3. 초기 admin 계정 자동 생성됨
```

### 재발 방지

```toml
# pyproject.toml — DuckDB 버전 범위 제한
duckdb>=1.5.0,<1.6.0
```

> DuckDB는 마이너 버전 간에도 DB 파일 포맷이 호환되지 않을 수 있으므로 버전 핀 필수

---

## BUG-18: 에디터 화면 폭 축소

| 항목 | 내용 |
|---|---|
| **증상** | 에디터 페이지의 화면 폭이 이전보다 줄어들어 보임 (max-width 1200px 제한) |
| **원인** | Phase 14 개발 중 `editor.html`의 템플릿 블록이 `{% block content_full %}`에서 `{% block content %}`로 잘못 변경됨 |
| **영향** | 에디터 UI가 전체 너비를 사용하지 못함 |

### base.html 블록 구조

```html
<!-- content: max-width 1200px 제한 (파일 목록 등 일반 페이지) -->
{% block content %}{% endblock %}

<!-- content_full: 전체 너비 (에디터 등 넓은 레이아웃 필요) -->
{% block content_full %}{% endblock %}
```

### 수정 내용

```html
<!-- 이전 (잘못된 블록) -->
{% block content %}

<!-- 이후 (올바른 블록) -->
{% block content_full %}
```

### 변경 파일

- `app/templates/editor.html` — `{% block content %}` → `{% block content_full %}`

---

## BUG-19: 긴 데이터 시 화면 늘어짐 + 흰 바탕 표시

| 항목 | 내용 |
|---|---|
| **증상** | data_id 등 긴 텍스트가 있으면 화면이 아래로 늘어지면서 특정 시점에 흰 바탕으로 의미없는 화면이 길게 표시됨 |
| **원인** | `detail-panel`에 flexbox 축소(`min-height: 0`)와 가로 오버플로우 제어 속성이 누락됨. 긴 텍스트가 컨테이너를 수평으로 확장시키면서 레이아웃 깨짐 |
| **영향** | 긴 데이터 항목 선택 시 화면 레이아웃이 비정상적으로 변함 |

### 수정 내용

```css
/* detail-panel: flexbox 축소 + 가로 오버플로우 방지 */
.detail-panel {
    flex: 1;
    min-height: 0;        /* ← 추가: flexbox에서 콘텐츠에 의한 강제 확장 방지 */
    overflow-y: auto;
    overflow-x: hidden;   /* ← 추가: 가로 스크롤 방지 */
    padding: 20px;
    background: #FAFAFA;
}

/* cardContainer: 긴 텍스트 줄바꿈 강제 */
#cardContainer {
    overflow-x: hidden;      /* ← 추가 */
    word-break: break-word;  /* ← 추가 */
}
```

### 추가 복원 CSS

Phase 14 개발 과정에서 누락된 인라인 편집 관련 CSS도 함께 복원:

| 셀렉터 | 내용 |
|---|---|
| `.item.inline-editing` | 편집 모드 녹색 테두리 |
| `.editable-value[contenteditable="true"]` | 편집 가능 필드 배경색 |
| `td.editable-value[contenteditable="true"]` | `display: table-cell` (테이블 레이아웃 유지) |
| `.inline-edit-ta` focus | 텍스트에어리어 포커스 스타일 |
| autosave indicator | 자동 저장 표시 |
| draft banner | 드래프트 복원 배너 |
| loading spinner | 로딩 스피너 |
| GCS 버튼 | 발행/취소 버튼 스타일 |

### 변경 파일

- `app/templates/editor.html` — CSS 속성 추가 및 복원

---

## BUG-20: 편집 종료/취소 후 이동 경로 오류 + CSRF 토큰 만료

| 항목 | 내용 |
|---|---|
| **증상 1** | "편집 종료" 클릭 시 에디터 페이지에 머물러 있음. 브라우저 뒤로가기 시 "CSRF 토큰이 유효하지 않습니다" 에러 |
| **증상 2** | "편집 취소" 시 TASK 폴더 루트(`/gcs/browse`)까지 이동 (날짜폴더가 아닌 상위 폴더) |
| **증상 3** | 편집 종료 후 재진입 시 이전 수정사항이 사라짐 (GCS에서 새로 로드) |
| **원인** | (1) 편집 종료 시 페이지 이동 미구현, (2) 취소 시 이동 URL에 날짜/task 정보 미포함, (3) open-edit이 항상 GCS에서 새로 로드 |
| **영향** | 편집 흐름 UX 저하, CSRF 에러로 인한 사용자 혼란 |

### 수정 내용

#### 편집 종료 (`releaseFileLock`)

```javascript
// 이전: Lock 해제 후 페이지에 머무름
isFileLockedByMe = false;
fileLockOwner = null;
updateFileLockUI();
showToast('편집 종료', 'info');

// 이후: Lock 해제 + 파일목록(날짜폴더)으로 forward navigation
if (EDIT_MODE === 'gcs' && GCS_DATE) {
    const taskParam = GCS_TASK ? `?task=${GCS_TASK}` : '';
    window.location.href = `${API_V1_STR}/gcs/browse/${GCS_DATE}${taskParam}`;
}
```

#### 편집 취소 (`discardWorkingCopy`)

```javascript
// 이전: TASK 루트로 이동
window.location.href = `${API_V1_STR}/gcs/browse`;

// 이후: 날짜폴더로 이동
const taskParam = GCS_TASK ? `?task=${GCS_TASK}` : '';
window.location.href = `${API_V1_STR}/gcs/browse/${GCS_DATE}${taskParam}`;
```

#### open-edit working copy 재사용

```python
# gcs.py open-edit 엔드포인트
if await gcs_edit_service.is_loaded(file_id):
    # 기존 working copy 재사용 (GCS 재로드 없음)
    meta = await gcs_edit_service.get_meta(file_id)
    total_rows = int(meta["total_rows"])
    resumed = True
else:
    # GCS에서 새로 로드
    total_rows = await gcs_edit_service.load_from_gcs(...)
```

#### task_id 전달

```python
# files.py view_file
gcs_path = meta.get("gcs_path", "")
for tid, tinfo in settings.GCS_TASKS.items():
    if gcs_path.startswith(tinfo["prefix"]):
        gcs_task = tid
        break
```

### CSRF 에러 해소 원리

| 시나리오 | 이전 | 이후 |
|---|---|---|
| 편집 종료 → 파일목록 | 페이지 유지 → 브라우저 Back → 캐시된 페이지의 CSRF 만료 | `window.location.href`로 정방향 이동 → 새 CSRF 토큰 발급 |

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/api/v1/endpoints/gcs.py` | open-edit: `is_loaded()` 체크, `resumed` 응답 필드 |
| `app/api/v1/endpoints/files.py` | `gcs_task` 템플릿 변수 추가 |
| `app/templates/editor.html` | `GCS_TASK` 변수, `releaseFileLock()` 이동 로직, `discardWorkingCopy()` URL 수정 |

---

## BUG-21: 한글 키 생성 시 마지막 글자 누락

| 항목 | 내용 |
|---|---|
| **증상** | 키 생성 시 "단일질문"을 입력하면 "단일질"까지만 저장됨 (마지막 글자 누락) |
| **원인** | 브라우저 `prompt()` 네이티브 다이얼로그가 한글 IME 조합 중 확인 버튼 클릭 시 마지막 조합 문자를 완성하지 않음 |
| **영향** | 한글 키를 정확한 이름으로 생성할 수 없음 |

### 원인 상세

```
사용자가 "단일질문" 입력 중:
1. "단일질" 입력 완료 (조합 확정)
2. "ㅁ" → "무" → "문" 조합 중 (IME 조합 상태)
3. 이 상태에서 OK 버튼 클릭 → prompt()가 조합 미완성 문자를 누락
4. 결과: "단일질" (마지막 "문" 누락)
```

### 수정 내용

`prompt()`를 커스텀 HTML 모달 다이얼로그로 교체:

```javascript
function showKeyInputDialog(message, onConfirm) {
    // 커스텀 오버레이 + 다이얼로그 생성
    const overlay = document.createElement('div');
    overlay.className = 'key-input-overlay';
    overlay.innerHTML = `
        <div class="key-input-dialog">
            <h4>${escapeHtml(message)}</h4>
            <input type="text" id="keyInputField" placeholder="키 이름 입력" />
            <div class="dialog-actions">
                <button class="btn-dialog-cancel">취소</button>
                <button class="btn-dialog-ok">확인</button>
            </div>
        </div>
    `;
    // ...
    // Enter 키 처리 시 IME 조합 상태 확인
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.isComposing) submit();
        if (e.key === 'Escape') close();
    });
}
```

### 핵심 포인트

| 항목 | 설명 |
|---|---|
| `e.isComposing` | `true`이면 IME 조합 중 → Enter 무시하여 조합 완성 대기 |
| `<input>` 요소 | 네이티브 `prompt()`와 달리 IME 조합 이벤트(`compositionstart`/`compositionend`)를 정상 처리 |
| 커스텀 모달 | 디자인 통일, 키보드 이벤트 완전 제어 가능 |

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/templates/editor.html` | `showKeyInputDialog()` 구현, `addKeyToSection()`에서 호출, CSS 추가 (`.key-input-overlay`, `.key-input-dialog`) |

---

## BUG-22: 키 삭제 후 취소 시 삭제된 키가 복원되지 않음

| 항목 | 내용 |
|---|---|
| **증상** | 편집 모드에서 `add_info.풀이` 딕셔너리의 "정답"이나 "해설" 키를 삭제한 후 "취소" 버튼을 클릭하면, 삭제된 키가 복원되지 않고 삭제된 상태로 유지됨 |
| **원인** | `deleteKeyFromSection()`에서 `row.remove()`로 DOM 요소를 완전히 제거하여, `cancelEdit()` 시 이미 DOM에서 사라진 행을 복원할 수 없음 |
| **영향** | 편집 취소 시 원본 데이터가 정확히 복원되지 않음. 의도치 않은 데이터 손실 가능 |

### 원인 상세

```
편집 흐름:
1. enterInlineEdit() → originalHtmlMap에 각 editable 요소의 원본 HTML 저장
2. deleteKeyFromSection('add_info.풀이', '정답') 호출
   → row.remove() 로 <tr> DOM 요소 완전 제거 ← 문제 지점
   → deletedKeys.add('add_info.풀이.정답')
3. cancelEdit() 호출
   → card.querySelectorAll('.editable-value[data-editing="true"]') 순회
   → 이미 DOM에서 제거된 행은 querySelectorAll에 포함되지 않음
   → 삭제된 키의 행이 복원되지 않음
```

### 수정 내용

#### 1. `deleteKeyFromSection()` — DOM 보존 (remove → hide)

```javascript
// 이전: DOM에서 완전 제거
const row = sectionBlock.querySelector(`tr[data-key="${CSS.escape(key)}"]`);
if (row) {
    row.remove();
}

// 이후: 숨김 처리 + 상태 마킹 (DOM 보존)
const row = sectionBlock.querySelector(`tr[data-key="${CSS.escape(key)}"]`);
if (row) {
    row.style.display = 'none';
    row.classList.add('pending-delete');
}
```

#### 2. `cancelEdit()` — 삭제 예정 행 복원 + 새로 추가된 행 제거

```javascript
// 삭제 예정(pending-delete) 행 복원: 다시 표시
card.querySelectorAll('.pending-delete').forEach(row => {
    row.style.display = '';
    row.classList.remove('pending-delete');
});

// 새로 추가된 행(new-key-row) 제거: 취소 시 원래 없던 행은 삭제
card.querySelectorAll('.new-key-row').forEach(row => {
    row.remove();
});
```

#### 3. `validateAllChanges()` / `collectInlineChanges()` — 숨겨진 행 필터링

```javascript
// 두 함수 모두 editable 순회 시 pending-delete 행 건너뛰기
editables.forEach(el => {
    if (el.closest('.pending-delete')) return;  // ← 추가
    // ... 기존 로직
});
```

### 동작 흐름 (수정 후)

| 시나리오 | 키 삭제 | 취소 | 저장 |
|---|---|---|---|
| 기존 키 삭제 | `display:none` + `pending-delete` | 행 표시 복원 | `deletedKeys`로 서버에 삭제 전달 |
| 새 키 추가 후 삭제 | `display:none` + `pending-delete` | `new-key-row` 행 제거 | `addedKeys`에서 제거됨 |
| 새 키 추가 (삭제 안 함) | — | `new-key-row` 행 제거 | `addedKeys`로 서버에 추가 전달 |

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/templates/editor.html` | `deleteKeyFromSection()` DOM 보존, `cancelEdit()` 복원 로직, `validateAllChanges()` / `collectInlineChanges()` 필터링 |

---

## BUG-23: 데이터 전환 시 화면 늘어남 (스크롤 위치 미초기화)

| 항목 | 내용 |
|---|---|
| **증상** | 텍스트가 긴 데이터를 아래 스크롤로 확인한 후 다른 데이터 ID를 선택하면, 화면이 아래로 늘어난 상태로 유지됨 |
| **원인** | `selectItem()`에서 새 항목 선택 시 `.detail-panel`의 `scrollTop`을 초기화하지 않아, 이전 항목에서 스크롤한 위치가 다음 항목에도 유지됨 |
| **영향** | 짧은 데이터 선택 시에도 화면 하단에 빈 공간이 보이거나, 콘텐츠가 보이지 않는 위치에서 시작됨 |

### 수정 내용

```javascript
// selectItem() — 새 항목 로드 전 스크롤 위치 초기화
const detailPanel = document.getElementById('detailPanel');
detailPanel.scrollTop = 0;
```

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/templates/editor.html` | `selectItem()`에서 `detailPanel.scrollTop = 0` 추가 |

---

## BUG-24: 편집 모드에서 문자열 개행문자(\n) 미표시

| 항목 | 내용 |
|---|---|
| **증상** | `content` 등 문자열 값에 포함된 개행문자(`\n`), 탭문자(`\t`)가 편집 모드에서 보이지 않음 |
| **원인** | `enterInlineEdit()`에서 문자열을 `el.textContent = rawValue`로 설정한 후 `contenteditable`을 사용. HTML에서 `contenteditable` 요소는 기본적으로 `white-space: normal`이므로 개행/탭 문자가 공백으로 collapse됨 |
| **영향** | 개행이 포함된 텍스트를 편집할 때 원본 구조를 확인할 수 없음. 의도치 않게 개행 삭제 가능 |

### 수정 내용

개행/탭 문자가 포함된 문자열은 `contenteditable` 대신 `<textarea>`로 전환하여 줄바꿈이 정확히 표시되도록 수정:

#### `enterInlineEdit()` — 카드 editable 요소

```javascript
// 이전: 모든 문자열에 contenteditable 사용
} else if (typeof rawValue === 'string') {
    el.textContent = rawValue;
    el.contentEditable = 'true';
}

// 이후: 개행/탭 포함 문자열은 textarea 사용
} else if (typeof rawValue === 'string') {
    if (/[\n\t\r]/.test(rawValue)) {
        const rows = Math.min(Math.max(rawValue.split('\n').length, 3), 20);
        el.innerHTML = '';
        const ta = document.createElement('textarea');
        ta.className = 'inline-edit-ta';
        ta.rows = rows;
        ta.value = rawValue;
        el.appendChild(ta);
        el.dataset.useTextarea = 'true';
    } else {
        el.textContent = rawValue;
        el.contentEditable = 'true';
    }
}
```

#### `content_meta` 섹션 — 동일 패턴 적용

```javascript
// 이전: 모든 문자열에 contenteditable
} else {
    metaHtml += `<td ... contenteditable="true">${escapeHtml(displayVal)}</td>`;
}

// 이후: 개행/탭 포함 문자열은 textarea
} else if (typeof v === 'string' && /[\n\t\r]/.test(v)) {
    const rows = Math.min(Math.max(v.split('\n').length, 2), 10);
    metaHtml += `<td ... data-use-textarea="true">
        <textarea class="inline-edit-ta" rows="${rows}">${escapeHtml(v)}</textarea>
    </td>`;
} else {
    metaHtml += `<td ... contenteditable="true">${escapeHtml(displayVal)}</td>`;
}
```

### 값 수집 호환성

기존 `collectInlineChanges()` / `validateAllChanges()` 코드가 `useTextarea` 분기에서 문자열을 올바르게 처리:

```javascript
if (el.dataset.useTextarea === 'true') {
    const ta = el.querySelector('.inline-edit-ta');
    const text = ta ? ta.value.trim() : '';
    // origType이 'string'이므로 else 분기로 진입
    try { value = JSON.parse(text); } catch { value = text; }
    // → JSON.parse 실패 시 textarea 텍스트 그대로 사용 (개행 보존)
}
```

### 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/templates/editor.html` | `enterInlineEdit()` 문자열 textarea 전환, `content_meta` 문자열 textarea 전환 |

---

## 변경 파일 전체 요약

| 파일 | BUG | 설명 |
|---|---|---|
| `app/api/v1/endpoints/files.py` | 16, 20 | TemplateResponse API 수정, gcs_task 전달 |
| `app/api/v1/endpoints/gcs.py` | 16, 20 | TemplateResponse API 수정, open-edit working copy 재사용 |
| `app/templates/editor.html` | 18, 19, 20, 21, 22, 23, 24 | content_full 복원, CSS 추가, 편집 흐름 이동, IME 모달, 키 삭제 취소 복원, 스크롤 초기화, 문자열 textarea |
| `pyproject.toml` | 17 | DuckDB 버전 핀 (>=1.5.0,<1.6.0) |

### Git 커밋 이력

| 커밋 | BUG | 설명 |
|---|---|---|
| `eaf9b1c` | 18, 19 | feat: 파일 단위 Lock + 편집 키 생성/삭제 + UI 복원 |
| `4f6c84c` | 16, 17 | fix: Starlette 1.0.0 TemplateResponse API 호환성 + DuckDB 버전 고정 |
| `60d3280` | 20, 21 | fix: 편집 종료/취소 흐름 개선 + 한글 키 입력 IME 수정 |
