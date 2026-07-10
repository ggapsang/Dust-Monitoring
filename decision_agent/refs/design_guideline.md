# 프론트엔드 디자인 지침

> 문서 버전: v1.0
> 작성일: 2026-05-04
> 적용 범위: AMR 분진 이상탐지 시스템의 모든 어드민/관제 웹 화면
> (`manual.html`, `auto.html`, `mock_sender` 관리 페이지, Dumopro Analysis 페이지 등)

---

## 1. 위치 및 우선순위

본 문서는 시스템 내 모든 프론트엔드 화면의 시각 언어를 통일하기 위한 단일 출처(single source of truth)다. 그동안 다음 위치에 분산되어 있던 규칙을 통합한다.

- `dumopro_analysis_app_plan.md` §7.1 디자인 기조
- `admin_manual_operation_spec.md` 부록 B 디자인 가이드
- `backend/static/admin/css/manual.css` (기준 토큰)
- `frontend/css/classic.css` (기준 토큰의 Dumopro판)
- `backend/tests/unit/test_auto_html_design.py` (자동 검증 게이트)

문서와 코드가 충돌할 경우 코드(특히 `manual.css` / `classic.css`)와 자동 검증 테스트가 우선한다.

---

## 2. 디자인 기조

본 시스템의 모든 어드민/관제 화면은 **Windows 9x/2000 시대의 클래식 어드민 UI 스타일**을 따른다.

### 2.1 의도

- **장시간 응시 피로 최소화**: 관제·운영 환경은 한 화면을 수 시간 응시한다. 채도·그라데이션·움직임은 시각 피로를 누적시킨다.
- **시스템 내 시각적 통일성**: `manual.html`, `auto.html`, `mock_sender` 관리 페이지가 동일한 비주얼 언어를 공유해야 한다. 페이지 간 이동 시 사용자가 "다른 시스템에 들어왔다"고 느끼지 않아야 한다.
- **정보 밀도 우선**: 장식 요소는 정보를 가린다. 표·라벨·수치가 1순위이고, UI 크롬(chrome)은 가능한 한 얇고 무채색이다.
- **프론트엔드 부채 차단**: 디자인 시스템이 페이지마다 다르게 자라는 것을 막기 위해 모든 신규 페이지는 기존 CSS 토큰을 import하여 재사용한다.

---

## 3. 시각 토큰 (필수 준수)

| 항목 | 지침 |
|---|---|
| 배경 | 순백(`#ffffff`) 단색. 그라데이션·패턴·질감 금지. |
| 보조 배경 | 회색 단색(`#f5f5f5`, `#e0e0e0` 등) 허용. 영역 구분 용도에 한함. |
| 글자색 | 순흑(`#000000`) 기본. 보조 정보는 회색 계열(`#444`, `#666`)까지만 허용. |
| 에러·경고 강조 | 어두운 빨강(`#a00000`, `#c00`) 정도. 채도 높은 형광색 금지. |
| 경고(warning) | 어두운 노랑/주황(`#c90`) 정도. |
| 폰트 | `MS Sans Serif`, `Tahoma`, `Courier New` 등 클래식 sans-serif / monospace 계열. |
| 본문 글자 크기 | 11~12px 기준. 표는 11px까지 허용. |
| 테두리 | 1px 단선(`#000`, `#999`, `#ccc`). 둥근 모서리 금지. 그림자 금지. |
| 섹션 구분 | `<fieldset> + <legend>` 또는 동등한 사각 박스 + 라벨. |
| 표 | `border-collapse: collapse`, 얇은 단선. 줄무늬는 `:nth-child(even)`만 허용, 호버 하이라이트는 클릭 가능 행에 한해 허용. |
| 버튼 | OS 기본 스타일에 가깝게. `border: 2px outset` 클래식 톤. 커스텀 컬러 버튼·아이콘 버튼 지양. |
| 아이콘/이모지 | 원칙적으로 사용하지 않는다. 필요한 경우 단색 기호(✔, ✗, →) 또는 텍스트 라벨로 제한. |
| 애니메이션 | 트랜지션·페이드·슬라이드 금지. 탭 전환·모달 열림 등 기능적 전환만 즉시(no transition) 반영. |

---

## 4. 절대 금지 사항

자동 테스트(`test_auto_html_design.py`)가 강제로 검증한다. PR이 머지되려면 이 규칙을 통과해야 한다.

| 항목 | 사유 |
|---|---|
| `border-radius` 양수값 | 둥근 모서리는 모던 UI 시그니처. 클래식 톤 파괴. (`0` / `0px`만 허용) |
| `box-shadow` | 그림자는 깊이감을 만들어 평면 정보 밀도를 해친다. |
| `linear-gradient` / `radial-gradient` | 채도 도입 경로. 단색 원칙 위반. |
| 새로운 CSS 토큰 도입 | `manual.css`에 이미 정의된 회색·검정 토큰을 재사용한다. 신규 색상 변수 추가는 본 지침 갱신을 동반한다. |

---

## 5. CSS 재사용 원칙

### 5.1 단일 진입점

신규 어드민 페이지(`auto.html` 등)는 반드시 `manual.css`를 link 태그로 import해야 한다. 페이지 고유 컴포넌트만 별도 .css 파일(`auto.css` 등)에 작성하되, **새 색상·새 모서리·새 그림자 도입은 금지**한다.

```html
<link rel="stylesheet" href="css/manual.css" />
<link rel="stylesheet" href="css/auto.css" />
```

### 5.2 표준 클래스 재사용 의무

| 용도 | 표준 클래스 | 정의 위치 |
|---|---|---|
| 일반 버튼 | `.btn` | `manual.css` |
| 주요 버튼 | `.btn .btn-primary` | `manual.css` |
| 보조 버튼 | `.btn .btn-secondary` | `manual.css` |
| 위험 버튼 | `.btn .btn-danger` | `manual.css` |
| 판정 배지 | `.judgment-badge` | `manual.css` |
| 상태 배지 | `.status-badge` | `manual.css` |
| 탭 버튼 | `.tab-btn`, `.tab-btn.active` | `manual.css` |
| 모달 | `.modal-overlay`, `.modal-content` | `manual.css` |
| 컨텍스트 메뉴 | `.context-menu` | `manual.css` |
| 토스트 | `.toast`, `#toast-container` | `manual.css` |

신규 페이지에서 같은 용도의 컴포넌트를 새로 작성하지 않는다.

### 5.3 레이아웃 표준 — manual.html 5영역 grid

`manual.html`이 채택한 5영역 grid(상단 네비 / 좌상단 Storage / 좌하단 Models / 메인 패널 / 하단 상태바)를 표준 레이아웃으로 둔다. `auto.html` 등 신규 페이지도 같은 5영역 골격에 패널 명칭만 교체한다.

---

## 6. 컴포넌트별 세부 규칙

### 6.1 표 (테이블)

- `border-collapse: collapse`
- 헤더: 회색 배경(`#ddd`), 1px 단선(`#999`), `position: sticky`로 스크롤 시 고정.
- 본문: 1px 단선(`#ccc`), 짝수행만 `#f5f5f5` 배경.
- 행 호버: 클릭 가능한 행에 한해 `#cde` 배경 허용.
- 선택된 행: `#cde` + `font-weight: bold`.
- 셀 너비 초과 텍스트: `text-overflow: ellipsis`, `max-width` 지정.

### 6.2 사이드 패널 / 트리뷰

- 좌측 사이드 패널은 상하 분할, 각각 독립 스크롤(초기 6:4 고정).
- 트리 들여쓰기: 16px.
- 트리 노드 호버·선택 표시는 `#eee` / `#cde` 회색 톤만 사용.

### 6.3 폼 입력

- `<input>`, `<select>`: 1px 단선(`#000` 또는 `#999`), 흰색 배경, monospace 폰트(숫자 입력 시).
- 라벨: 좌측 110px 고정폭 grid.

### 6.4 모달

- 오버레이: `rgba(0,0,0,0.3)` 단순 음영. 블러 금지.
- 컨텐츠: 흰색 배경, 2px 단선(`#999`). 둥근 모서리·그림자 금지.

### 6.5 컨텍스트 메뉴

- 1px 단선(`#999`), `min-width: 140px`, 폰트 12px.
- `.danger` 클래스로 위험 액션은 `#c00` 텍스트.

### 6.6 토스트

- 우측 하단 고정(`bottom: 32px`, `right: 12px`).
- 자동 사라짐만 허용. 페이드 인/아웃 금지(즉시 표시·즉시 제거).

---

## 7. 차트 색상 예외

캔들차트·히트맵·anomaly score 그래프 등 **데이터 표현 영역에 한해** 채도 있는 색상 사용을 허용한다. 단, 다음 조건을 만족해야 한다.

- 배경·프레임·축·범례 박스 등 차트의 UI 크롬(chrome)은 본 지침의 모노톤 규칙을 따른다.
- 데이터 색상은 첨부 `dust_candle_final.html`의 팔레트를 기준으로 한다.

캔들차트 표준 팔레트 (`frontend/js/chart.js`):

| 요소 | 색상 |
|---|---|
| 박스 몸통 | `#6b96c8` (차분한 파랑) |
| 박스 테두리·중앙선·수염 | `#000000` |
| Outlier | `#c89040` (어두운 주황) |
| Extreme | `#a00000` (어두운 빨강) |
| 이동평균선 | `#1a7a3a` (어두운 녹색) |
| 추세선 | `#7030a0` (어두운 보라) |
| 예측 밴드 | `rgba(112,48,160,0.15)` |
| Residual 초과 강조 | `#ff6600` |
| 라이브 캔들 몸통 | `#c6c0a0` |

Anomaly heatmap은 PatchCore 표준대로 JET 컬러맵을 사용한다.

---

## 8. 추론 결과 비교 뷰 — 알고리즘별 분기

알고리즘에 따라 비교 뷰 구성이 다르다.

| 알고리즘 | 비교 뷰 | 근거 |
|---|---|---|
| 오토인코더 | 5단(원본 / 복원 / 에러맵 / 오버레이 / 컨투어) | reconstruction이 존재함 |
| 메모리뱅크(PatchCore) | 2단(원본 / Anomaly Heatmap) | reconstruction 없음 |

프론트엔드는 `frame.reconstructed_url == null && heatmap_url != null` 조건으로 자동 분기한다(`admin_manual_operation_spec.md` §0.1).

---

## 9. 자동 검증

다음 테스트가 CI에서 디자인 지침을 강제한다.

```
backend/tests/unit/test_auto_html_design.py
backend/tests/unit/test_stage9_settings_drift.py
```

### 9.1 `test_auto_html_design.py` — 부록 B 디자인 가이드 본체

- `auto.html`이 `manual.css`를 import하는가
- `.btn`, `.btn-primary` 등 표준 버튼 클래스를 사용하는가
- `.judgment-badge`, `.status-badge`를 재사용하는가
- `auto.css`에 `border-radius` 양수값이 없는가
- `auto.css`에 `box-shadow`가 없는가
- `auto.css`에 `linear-gradient` / `radial-gradient`가 없는가
- 14칼럼 테이블 헤더가 모두 존재하는가
- 사이드 패널 4탭(`inference`, `bank`, `alerts`, `data`)이 존재하는가
- 추론 결과 비교 뷰가 2단(원본 / Anomaly Heatmap) 구성인가 — `Reconstructed` 미포함

### 9.2 `test_stage9_settings_drift.py` — 설정 모달·drift 프론트엔드 구조 검증

- `auto.html`에 설정 진입 버튼(`#btn-settings`)과 설정 모달(`#settings-modal`)이 존재하는가
- 설정 모달에 보존 정책(`#setting-retention-policy`)·drift 산식(`#setting-drift-metric`) UI가 모두 노출되는가
- `api.js`에 `settings` 네임스페이스와 `bank.delete` 함수가 추가되어 있는가

신규 페이지를 추가할 경우 동일 패턴의 검증 테스트를 함께 추가한다.

---

## 10. 의사결정 시 체크리스트

새 컴포넌트·새 화면을 만들 때 PR 전 다음을 확인한다.

- [ ] 같은 용도의 표준 클래스가 `manual.css`에 이미 있는가? 있으면 재사용한다.
- [ ] 새 색상을 도입하지 않았는가? 도입했다면 본 지침을 갱신했는가?
- [ ] `border-radius`, `box-shadow`, `gradient`를 사용하지 않았는가?
- [ ] 애니메이션/트랜지션을 추가하지 않았는가?
- [ ] 이모지·컬러풀 아이콘을 추가하지 않았는가?
- [ ] 5영역 grid 레이아웃을 따르고 있는가?
- [ ] 차트가 있다면 UI 크롬은 모노톤이고 데이터 색상만 표준 팔레트를 사용하는가?
- [ ] 자동 검증 테스트(`test_*_html_design.py`)가 통과하는가?
