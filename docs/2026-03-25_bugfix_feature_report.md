# Phase 14.2 버그 수정 및 기능 개선 보고서

> **날짜**: 2026-03-25
> **영향 파일**: `base.html`, `editor.html`, `render_service.py`

---

## 1. 버그 수정

### BUG-25: 키 순서 변경 버튼(▲/▼) 미동작

| 항목 | 내용 |
|---|---|
| **증상** | 편집모드에서 ▲/▼ 버튼 클릭 시 아무 반응 없음 |
| **원인** | `moveKeyUp()`/`moveKeyDown()`에서 `table.insertBefore(row, prevRow)` 호출 시, 브라우저가 자동 생성하는 `<tbody>`를 고려하지 않아 `NotFoundError` 발생. `<tr>`은 `<table>`의 직접 자식이 아니라 `<tbody>`의 자식이므로, `<table>`에서 `insertBefore`를 호출하면 대상 노드를 찾을 수 없음 |
| **수정** | `table.insertBefore()` → `row.parentNode.insertBefore()` (`<tbody>` 참조)로 변경. `trackSectionOrder()`도 `table tbody` 기준으로 행 조회하도록 수정 |
| **수정 파일** | `editor.html` — `moveKeyUp()`, `moveKeyDown()`, `trackSectionOrder()` |

### BUG-26: 줄바꿈 심볼(↵) 시인성 부족 + 편집모드 개행 처리 오류

| 항목 | 내용 |
|---|---|
| **증상 1** | 뷰모드에서 `↵` 심볼이 배경과 구분되지 않아 눈에 잘 띄지 않음 |
| **증상 2** | 뷰모드에서 `↵` 심볼만 표시되고 실제 줄바꿈이 발생하지 않음 |
| **증상 3** | 편집모드에서 `\n`이 실제 개행으로 처리되어, 원본 문자열의 `\n` 위치를 확인할 수 없음 ("안녕하세요\n박환입니다.\n" → 리터럴 문자로 보여야 함) |
| **수정 1** | `.nl-symbol` CSS: 인디고(#E8EAF6) → 주황(#EF6C00), 흰색 텍스트, 볼드체로 변경 |
| **수정 2** | `render_service.py`: `NEWLINE_SYMBOL`에 `<br>` 추가 → `↵` 심볼 + 실제 줄바꿈 동시 표시 |
| **수정 3** | `editor.html`: 편집모드에서 `\n`, `\t`, `\r`, `\\`를 리터럴 문자열로 표시. `contenteditable` 요소에 `data-has-escapes` 속성 부여. 저장 시 `unescapeEditValue()` 함수로 리터럴 → 실제 이스케이프 문자 복원 |
| **수정 파일** | `base.html` (CSS), `render_service.py` (NEWLINE_SYMBOL), `editor.html` (enterInlineEdit, collectInlineChanges, validateAllChanges, cancelEdit, unescapeEditValue 추가) |

### BUG-27: KaTeX 수식이 포함된 데이터에서 스크롤이 `<html>` 태그 밖으로 확장

| 항목 | 내용 |
|---|---|
| **증상** | `EHT_3077_10004_Q` 등 수식($...$)이 포함된 데이터 선택 시, 카드 콘텐츠 아래에 불필요한 빈 공간이 생기며 `<html>` 태그 범위 밖까지 스크롤 가능 |
| **영향 범위** | KaTeX 수식이 포함된 모든 데이터 |
| **원인** | KaTeX 렌더링 시 생성되는 `.katex-mathml` 요소가 `position: absolute`로 설정되지만, 부모 체인에 `position: relative`를 가진 요소가 없어 **containing block이 `<html>`까지 올라감**. 이때 `.katex-mathml`의 static position(스크롤 컨테이너 내부 깊은 위치)이 `<html>` 기준으로 계산되면서 `<html>` 요소의 overflow를 확장시킴 |
| **수정** | `.item`에 `position: relative` 추가 (카드 레벨 containing block 생성) + `.katex`에 `position: relative` 추가 (KaTeX 래퍼 레벨 containing block 생성) |
| **수정 파일** | `base.html` — `.item`, `.katex` CSS |
| **핵심 원리** | `position: absolute` 요소는 가장 가까운 positioned 조상을 containing block으로 사용. positioned 조상이 없으면 `<html>`(initial containing block)이 되어, 스크롤 컨테이너 내부의 절대 위치가 문서 전체의 overflow에 영향을 줌 |

---

## 2. 기능 개선

### 키 순서 변경 버튼 UI 재배치

| 항목 | 내용 |
|---|---|
| **변경 전** | ▲/▼ 버튼이 키 이름 오른쪽에 `margin-left`로 배치 |
| **변경 후** | ▲/▼ 버튼을 `.move-key-wrap` 컨테이너로 감싸 키 이름 **왼쪽 테두리에 세로 스택**으로 배치 |
| **수정 파일** | `base.html` (`.move-key-wrap`, `.btn-move-key` CSS), `render_service.py` (HTML 구조 변경), `editor.html` (편집모드 진입/취소 시 `.move-key-wrap` display 토글) |

### 방어적 CSS 보강 (추가 적용)

| 대상 | 수정 내용 | 파일 |
|---|---|---|
| `table.info-table` | `table-layout: fixed` + `overflow-wrap: break-word` + `word-break: break-word` | `base.html` |
| `table.info-table td` | `overflow: hidden` | `base.html` |
| `.meta-image img` | `max-height: 800px` + `object-fit: contain` | `base.html` |
| `.katex-display` | `overflow-x: auto; overflow-y: hidden` | `base.html` |
| `.meta-inline` | `overflow: hidden; vertical-align: top` | `base.html` |

---

## 3. 수정 파일 요약

| 파일 | 변경 사항 |
|---|---|
| `app/templates/base.html` | `.item` position:relative, `.katex` position:relative, `.nl-symbol` 색상 변경, `.info-table` 방어 CSS, `.meta-image img` 크기 제한, `.katex-display` overflow, `.meta-inline` overflow, `.move-key-wrap`/`.btn-move-key` CSS 추가 |
| `app/templates/editor.html` | `moveKeyUp/Down` tbody 수정, `trackSectionOrder` tbody 수정, `enterInlineEdit` 이스케이프 처리 + `.move-key-wrap` 토글, `cancelEdit` 이스케이프 정리 + `.move-key-wrap` 토글, `collectInlineChanges`/`validateAllChanges` 이스케이프 복원, `unescapeEditValue()` 함수 추가 |
| `app/services/render_service.py` | `NEWLINE_SYMBOL`에 `<br>` 추가, ▲/▼ 버튼을 `.move-key-wrap` 컨테이너로 감싸 키 이름 앞에 배치 |

---

## 4. 핵심 교훈

### DOM 구조 이해
- 브라우저는 `<table>` 내부에 자동으로 `<tbody>`를 삽입함. DOM 조작 시 `element.parentNode`를 통해 실제 부모를 참조해야 안전
- `position: absolute` 요소의 containing block은 가장 가까운 positioned 조상. positioned 조상이 없으면 `<html>`까지 올라가 문서 전체의 스크롤에 영향

### CSS Containing Block
- KaTeX 같은 외부 라이브러리가 `position: absolute`를 사용하는 경우, 반드시 적절한 위치에 `position: relative`로 containing block을 생성해야 함
- 스크롤 컨테이너 내부의 `position: absolute` 요소가 상위 컨테이너의 overflow를 확장시킬 수 있음

### 편집모드 이스케이프 처리
- `contenteditable`에서 `\n` 등의 이스케이프 시퀀스를 리터럴 문자로 표시하려면 이중 이스케이프(`\\n`) 필요
- 저장 시 `unescapeEditValue()`로 리터럴 → 실제 이스케이프 문자 복원
