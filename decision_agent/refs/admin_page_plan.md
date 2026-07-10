# Decision Agent 어드민 페이지 구성 계획

> 작성일: 2026-05-04
> 상태: 기획 (구현 미착수)
> 적용 디자인: [design_guideline.md](./design_guideline.md) (Windows 9x/2000 클래식 어드민 스타일)

---

## 1. 목적

Decision Agent 운영 중 다음 4가지 작업을 수동으로 처리하기 위한 단일 어드민 페이지를 둔다.

1. **role_mapping / alarm_mapping 캐시 수동 리로드** — 자동 hot-reload 대신 운영자 버튼 클릭으로 트리거 (plan 문서 v2 항목 단순화)
2. **role_mapping / alarm_mapping 편집** — 코드 변경 없이 매핑을 갱신
3. **decision_record 모니터링** — 최근 판정 결과 / pending 누적 추적
4. **stuck pending 수동 개입** — 부분 도착으로 멈춘 record 강제 판정 (v2 타임아웃 정책 대신 운영자 판단)

자동 모니터링·알람·자체 헬스체크는 v1 범위에서 **제외**한다.

---

## 2. 디자인 컨셉 요약 (design_guideline.md 발췌)

본 페이지가 따라야 하는 핵심 규칙:

| 항목 | 적용 |
|---|---|
| 배경 | 순백 `#ffffff` 단색 |
| 글자 | 순흑 `#000000`, 보조 `#444`/`#666` |
| 강조 | 에러 `#a00000`/`#c00`, 경고 `#c90` |
| 폰트 | `MS Sans Serif`, `Tahoma`, `Courier New` 11~12px |
| 테두리 | 1px 단선, 둥근 모서리 금지, 그림자 금지 |
| 섹션 | `<fieldset> + <legend>` |
| 표 | `border-collapse: collapse`, 짝수행 `#f5f5f5`, 클릭 가능 행 hover `#cde` |
| 버튼 | `border: 2px outset` 클래식 |
| 아이콘/이모지 | 사용 안 함. 필요 시 `✔ ✗ →` 정도 텍스트 기호 |
| 애니메이션 | 금지 |

**금지 항목:** `border-radius` 양수, `box-shadow`, gradient, 신규 색상 토큰.

**레이아웃:** 본 페이지는 design_guideline.md §5.3의 **5영역 grid**를 따른다.

---

## 3. 전체 레이아웃 (5영역 grid)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ [Top Nav]                                                               │
│  Decision Agent Admin     [Reload role_mapping] [Reload alarm_mapping]  │
├──────────────────────┬──────────────────────────────────────────────────┤
│ [좌상단]             │ [메인 패널]                                      │
│  role_mapping        │   decision_record 브라우저 (탭 전환)             │
│  (3행 표 + 편집)     │   ┌─────────────────────────────────────────┐    │
│                      │   │ [Recent] [Pending] [Stuck (>5m)]        │    │
│                      │   │ ─────────────────────────────────────── │    │
│                      │   │ id │ station │ ts │ a │ o │ s │ final  │    │
│                      │   │ ...                                     │    │
│                      │   │ ...                                     │    │
│                      │   └─────────────────────────────────────────┘    │
├──────────────────────┤                                                  │
│ [좌하단]             │                                                  │
│  alarm_mapping       │                                                  │
│  (12행 표 + 편집)    │                                                  │
│                      │                                                  │
├──────────────────────┴──────────────────────────────────────────────────┤
│ [Status Bar]  pending: 12  decided/h: 84  cache loaded: 14:23  DB: OK   │
└─────────────────────────────────────────────────────────────────────────┘
```

design_guideline.md §5.3의 "manual.html 5영역 grid"를 그대로 차용. 패널 명칭만 Decision Agent 도메인에 맞게 교체한다.

---

## 4. 영역별 구성 상세

### 4.1 Top Nav (헤더)

- 좌측: 페이지 타이틀 `Decision Agent Admin` (단순 텍스트, 12px bold)
- 우측: 캐시 리로드 버튼 2개
  - `[Reload role_mapping]` — POST /admin/api/reload/role-mapping → role_resolver 강제 refresh
  - `[Reload alarm_mapping]` — POST /admin/api/reload/alarm-mapping → judge 강제 refresh
- 클래스: `.btn`, `.btn-primary` (design_guideline.md §5.2 표준 클래스 재사용)
- 작업 결과는 Toast로 표시 (`#toast-container`, design_guideline.md §6.6)

### 4.2 좌상단: role_mapping 패널

- `<fieldset><legend>role_mapping</legend>` 컨테이너
- 표 (3행 고정):

  | detection_role | component_name | updated_at | |
  |---|---|---|---|
  | static_dust | anomaly_detection | 2026-05-04 14:00 | [Edit] |
  | dynamic_dust | object_detection | 2026-05-04 14:00 | [Edit] |
  | iot_sensor | sensor_analysis | 2026-05-04 14:00 | [Edit] |

- `[Edit]` 클릭 → 모달(`.modal-overlay`+`.modal-content`)에서 `component_name` 드롭다운으로 변경
  - 드롭다운 옵션: `anomaly_detection`, `object_detection`, `sensor_analysis`
  - 저장 시 PATCH /admin/api/role-mapping/{role} → role_resolver 자동 refresh

### 4.3 좌하단: alarm_mapping 패널

- `<fieldset><legend>alarm_mapping (12 rows)</legend>` 컨테이너
- 표 12행 컴팩트(11px font, 줄무늬 짝수행 `#f5f5f5`):

  | iot | static | dynamic | final | |
  |---|---|---|---|---|
  | normal | normal | normal | normal | [Edit] |
  | normal | normal | abnormal | caution | [Edit] |
  | … (12행) |

- `[Edit]` → 모달에서 `final_decision` 드롭다운(normal/caution/warning) 변경
  - 입력 3컬럼(iot/static/dynamic)은 truth table 키이므로 잠금
  - 저장 시 PATCH /admin/api/alarm-mapping/{id} → judge 자동 refresh
- `final_decision` 셀에 status-badge 클래스 (`warning`은 `#a00000` 텍스트)

### 4.4 메인 패널: decision_record 브라우저

- 상단 탭(`.tab-btn`):
  - `[Recent]` — 최근 100건 (decided 우선, 시간 역순)
  - `[Pending]` — `final_decision='pending'`인 모든 행
  - `[Stuck]` — `observation_timestamp < NOW() - 5분` AND `final_decision='pending'`
- 아래 표 (.judgment-table):

  | id (8자리) | station_id | obs ts | anomaly | object | sensor | final | decided_at | sent_at | |
  |---|---|---|---|---|---|---|---|---|---|
  | 1e4529f0 | ST-001 | 14:23:01 | normal | normal | normal | **normal** | 14:23:05 | 14:23:06 | |
  | a8b2c100 | ST-RUNTIME | 14:25:00 | abnormal | abnormal | warning | **warning** | 14:25:02 | — | |

- `final` 셀: design_guideline.md §5.2 `.judgment-badge` 재사용
  - normal: 기본 검정
  - caution: `#c90` 다크 옐로
  - warning: `#a00000` 다크 레드 + `font-weight: bold`
- `sent_at`이 NULL이면 `—` 표시
- 행 클릭(`#cde` 호버) → 우측 보조 패널 또는 모달로 row 상세 표시 (raw 컬럼 dump, mapping_id, channel_result 원본 enum 값)
- 페이지네이션: 단순 prev/next (table footer), 페이지당 100행

#### 4.4.1 Stuck 탭에서의 수동 개입

- `[Stuck]` 탭에서 각 행 우측에 `[Force Decide]` 버튼 추가
- 클릭 시 모달:
  - 현재 채널 값 표시 (anomaly=normal, object=pending, sensor=normal)
  - "이 record를 어떤 final_decision으로 강제할까요?" 드롭다운(normal/caution/warning)
  - 저장 시 POST /admin/api/decisions/{id}/force → final_decision UPDATE + 별도 audit 컬럼(?) 기록
- audit 컬럼은 v1 스키마에 없음 → 도입 시 ADD COLUMN `forced_at TIMESTAMPTZ`, `forced_by VARCHAR(50)` (decision DB DDL 변경)

### 4.5 Status Bar (하단)

- 단일 행(11px monospace, `#444`):
  ```
  pending: {n}    decided/h: {n}    cache loaded: {hh:mm}    DB: OK | DOWN
  ```
- 5초 주기 폴링(JS `setInterval`, 트랜지션 없음 — 즉시 갱신)
- DB 연결 끊김 시 `DB: DOWN` `#a00000`
- pending이 임계치(예: 50) 초과 시 숫자만 `#c90`

---

## 5. 구현 스택

| 컴포넌트 | 선택 | 사유 |
|---|---|---|
| HTTP 서버 | FastAPI + uvicorn | SocketDaim과 동일 (이미 `requirements.txt`에 있는 패턴) |
| 템플릿 | Jinja2 | FastAPI 표준 |
| CSS | 자체 `admin.css` (manual.css의 핵심 토큰만 옮겨 작성) | SocketDaim의 manual.css에 직접 의존하면 cross-repo 결합도 증가. 처음부터 본 페이지에 필요한 클래스만 정의하는 게 단순. design_guideline.md §3 토큰을 동일한 값으로 적용. |
| JS | Vanilla (no framework) | 동작은 단순(폼 submit, fetch, 5초 polling). 프레임워크 도입 부담 회피 |
| 권한 | v1: 사내 네트워크 전제, no auth | dev 환경. 운영 진입 시 별도 검토 (Basic auth 또는 reverse proxy 인증) |

---

## 6. 디렉토리 구조 (구현 시)

```
c:\decision_agent\
├── src\decision_agent\
│   ├── ... (기존 모듈)
│   └── admin\
│       ├── __init__.py
│       ├── app.py            # FastAPI app, route 정의
│       ├── routes\
│       │   ├── pages.py      # GET / (HTML 렌더)
│       │   ├── reload.py     # POST /admin/api/reload/{name}
│       │   ├── role_mapping.py   # GET / PATCH role_mapping
│       │   ├── alarm_mapping.py  # GET / PATCH alarm_mapping
│       │   ├── decisions.py  # GET decisions (recent/pending/stuck), POST force
│       │   └── status.py     # GET /admin/api/status (status bar 폴링용)
│       ├── templates\
│       │   ├── base.html
│       │   ├── index.html    # 5영역 grid 본체
│       │   └── _modal_*.html # 작은 partial들
│       └── static\
│           ├── css\admin.css
│           └── js\admin.js   # 탭 전환, 모달 open/close, 5초 폴링
├── tests\
│   └── test_admin.py         # FastAPI TestClient 기반 라우트 테스트
└── docker-compose.yml         # 포트 9107 노출 추가
```

**main.py 변경:** poller task와 admin server task를 `asyncio.gather`로 동시 실행. 기동/종료 lifecycle은 그대로 stop_event로 통일.

---

## 7. API 엔드포인트 명세

| Method | Path | 설명 | Body | Response |
|---|---|---|---|---|
| GET | `/` | 어드민 페이지 (HTML) | — | `text/html` |
| GET | `/admin/api/status` | 상태 바 데이터 | — | `{pending,decided_per_hour,cache_loaded_at,db_ok}` |
| POST | `/admin/api/reload/role-mapping` | role_resolver.refresh() | — | `{ok,loaded_at}` |
| POST | `/admin/api/reload/alarm-mapping` | judge.load() | — | `{ok,rows,loaded_at}` |
| GET | `/admin/api/role-mapping` | 3행 조회 | — | `[{detection_role,component_name,updated_at},...]` |
| PATCH | `/admin/api/role-mapping/{role}` | component_name 변경 후 자동 refresh | `{component_name}` | `{ok}` |
| GET | `/admin/api/alarm-mapping` | 12행 조회 | — | `[{id,iot,static,dynamic,final,description},...]` |
| PATCH | `/admin/api/alarm-mapping/{id}` | final_decision 변경 후 자동 refresh | `{final_decision}` | `{ok}` |
| GET | `/admin/api/decisions?tab={recent\|pending\|stuck}&page={n}` | decision_record 조회 | — | `{rows,total,page}` |
| POST | `/admin/api/decisions/{id}/force` | stuck record 강제 판정 | `{final_decision}` | `{ok}` |

모든 PATCH/POST는 성공 시 `200 {ok:true,...}`, 실패 시 `4xx/5xx` + `{error}`. 클라이언트는 toast로 결과 표시.

---

## 8. 권한 / DB role 영향

현재 Decision Agent는 `decision_agent_role`로 DB에 접속한다. 본 어드민이 추가로 필요한 권한:

| 작업 | 필요 권한 | 현재 grant 상태 |
|---|---|---|
| role_mapping SELECT | 이미 있음 | ✔ |
| role_mapping UPDATE | 신규 | init_db.sql에 GRANT UPDATE ON role_mapping 추가 필요 |
| alarm_mapping SELECT | 이미 있음 | ✔ |
| alarm_mapping UPDATE | 신규 | init_db.sql에 GRANT UPDATE ON alarm_mapping 추가 필요 |
| decision_record force-decide | `final_decision`/`decided_at`/`mapping_id` UPDATE | 이미 있음 ✔ |

→ init_db.sql에 2줄 GRANT 추가가 본 페이지의 유일한 스키마 영향.

---

## 9. 구현 순서 (착수 시)

1. **HTTP 서버 통합** — FastAPI app skeleton, main.py에 task 추가, /admin/api/status 1개 엔드포인트만 먼저
2. **CSS + 5영역 grid 레이아웃** — base.html, admin.css, 빈 페이지 렌더 확인
3. **role_mapping 패널** — GET + PATCH + 모달, reload 버튼
4. **alarm_mapping 패널** — GET + PATCH + 모달, reload 버튼
5. **decision_record 브라우저** — Recent 탭 먼저 (가장 단순)
6. **Pending / Stuck 탭** — Stuck 탭과 Force Decide 모달
7. **Status bar 폴링**
8. **테스트** — pytest+TestClient로 각 API 회귀 테스트, 디자인 검증 테스트는 SocketDaim의 `test_auto_html_design.py` 패턴을 따름 (border-radius/box-shadow/gradient 부재 검증)

---

## 10. v1 범위 외 (의도적으로 빼는 것)

- 인증/권한 (사내망 전제)
- 다국어 (한국어 한정)
- 사용자별 audit log (force-decide의 `forced_by`만 추가하지만 SSO 연동은 안 함)
- 자체 헬스체크 / 메트릭 export
- 알림(Slack 등) 발송
- 다중 Decision Agent 인스턴스 운영 시의 캐시 동기화 (현재는 단일 인스턴스 가정)
