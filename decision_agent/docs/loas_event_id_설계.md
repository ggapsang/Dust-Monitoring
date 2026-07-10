# LOAS event_id 산출 & 데이터 결합 설계 (설계 전용 · 구현 없음)

> 목적: LOAS MariaDB(`3.데이터 매핑정보`) 적재 행의 **`event_id`(검사 결과 코드)** 를
> `dust_inspection.dust_value` 와 PoolerTran REST 결과 `score` **2입력**으로 산출하는 규칙과,
> 그 값을 dust 측정행/이미지와 결합해 LOAS 1행을 조립하는 방향을 설계한다.
> 분석 근거: `SocketDaim/init_db.sql`(dust_inspection), `PoolerTran` REST 결과(transfer_result),
> 명세서 `LOAS_ARQOS_분진센서_데이터_명세서_v0.1.xlsx > 3.데이터 매핑정보`.

---

## 0. 핵심 설계 결정 (전제)

| # | 결정 | 비고 |
|---|---|---|
| D1 | `event_id` 는 **`dust_value` + `score` 2입력**으로 산출 | decision_agent의 3채널 truth table을 **LOAS 경로에서 우회** |
| D2 | decision_agent `final_decision` 은 **LOAS event_id에 사용하지 않음** | ⚠️ "판정 권위 우회" — §7-1 합의 필요 |
| D3 | 산출 규칙 = **설정 가능한 2D 밴드 매트릭스** | 임계·매트릭스를 config로(코드 무수정 튜닝) |
| D4 | LOAS 행 단위 = **dust_inspection 측정 1건** | score/image는 측정행에 enrich(broadcast) |
| D5 | event_id 도메인 = **{0 정상, 1 주의, 2 경고, 3 위험}** | 위험(3)은 "센서 확산 확인 + 비전 강함"으로 산출(§3.4) |

이 결정의 가장 큰 이점: **event_id가 `decision_record`(decision_db)를 더 이상 필요로 하지
않으므로**, 크로스-DB 조인 + `station_id`(7-tuple)+시각 상관 매칭이 **제거**된다. 두 입력
(`dust_value`, `score`)이 모두 **gateway_db 안**(dust_inspection · transfer_result)에 존재한다.

---

## 1. 입력 정의

| 입력 | 출처 | 타입 | 단위(granularity) | 의미 |
|---|---|---|---|---|
| `dust_value` | `dust_inspection.dust_value` | DOUBLE | **측정 1건/행 (fine)** | 분진 측정값(센서). 센서 자체 레벨링(`dust_alarm`)은 **사용 안 함** — raw 값에 자체 임계 적용 |
| `score` | PoolerTran REST 결과(`transfer_result.score`) | float | **waypoint 배치당 1건 (coarse)** | 비전/AI 이상 점수. 높을수록 이상(가정 §7-2 확인) |

- **신호 특성(도메인 사실)**:
  - `score`(비전) = **고민감(high sensitivity)** — 국소 분진도 조기 탐지. 단 오탐 가능.
  - `dust_value`(센서) = **고특이도·저민감(high specificity)** — 센서는 정확하나 **측정범위가 좁아,
    분진 농도가 높아 주변까지 확산될 때만 값이 상승**한다. 즉 **상승 = "실제 + 심각(확산)" 신호**라
    신뢰도가 높지만, 국소/저농도 분진은 미검(정상으로 읽힘)할 수 있다.
  - → **상호 보완**: 탐지는 OR(둘 중 하나로도 경보 가능), 최고 심각도(위험)는 AND(둘 다 동의)로.
    `dust_value`는 "게이트"가 아니라 **심각도 에스컬레이션 신호**로 쓴다(§3.3).
- ⚠️ **`dust_alarm` 사용 금지**: `0=Fault,1=Maint,2=Alert,3=Normal`(센서 상태, 순서 반대)이라
  event_id(0정상…)와 무관.

---

## 2. 출력 정의

| event_id | 의미 | 산출 |
|---|---|---|
| 0 | 정상 | 기본 |
| 1 | 주의 | 매트릭스 |
| 2 | 경고 | 매트릭스 |
| (3) | 위험 | **옵션**(§3.4) — 활성 시에만 |

---

## 3. event_id 산출 규칙 — 2D 밴드 매트릭스

### 3.1 절차 (3단계)
```
① dust_value → dust_band   (임계 dust_thresholds[] 로 구간화)
② score      → score_band  (임계 score_thresholds[] 로 구간화)
③ event_id = MATRIX[dust_band][score_band]
```
- 모든 경계·매트릭스 셀은 **config 값**(코드 상수 아님). alarm_mapping과 같은 "표로 조정" 철학.

### 3.2 밴드 정의(기본 권장)
- **dust_band (2구간)**: 임계 `T_dust` 1개 → `정상(≤T_dust)`, `상승(>T_dust)`
- **score_band (3구간)**: 임계 `T_s1 < T_s2` → `낮음(<T_s1)`, `중간(<T_s2)`, `높음(≥T_s2)`
- (확장 가능: dust 3구간/ score 4구간 등 — config로 차원만 늘리면 매트릭스도 확장)

### 3.3 권장 매트릭스 — 센서 물리 반영(에스컬레이션 semantics)

신호 특성(§1)에 근거: **비전(score)으로 탐지(고민감), 센서(dust_value) 상승으로 심각도 상향
(고특이도).** dust_value는 게이트가 아니라 **에스컬레이션 신호**다.

```
              score 낮음   score 중간   score 높음
dust 정상         0           1           2
dust 상승         2           2           3
```

| 칸 | 의미 |
|---|---|
| dust 정상 행 | **비전 주도** — 센서가 못 닿는 국소 분진도 score로 경보(0/1/2). 단 센서 미확인이라 **위험(3)까지는 안 올림**(비전 오탐 과대경보 억제) |
| dust 상승 행 | **센서가 확산성 심각 분진 확인** — 비전이 약해도 **최소 경고(2)**, 비전도 높으면 **위험(3)** |

- 설계 원리: **탐지 = OR**(어느 한 신호로도 경보), **위험(3) = AND**(센서 확인 + 비전 강함).
  → 비전 오탐(센서 정상)은 위험으로 안 가고, 센서가 확인하면 심각도를 끌어올린다.
- 셀 값은 config로 조정 가능(예: `dust 상승 × score 낮음`을 더 보수적으로 3으로 둘 수도).
- 이 설계는 **dust_value가 "항상 정상"이 아니라 "심각 시 상승"** 이라는 사실에 기반하므로,
  앞서의 천장 문제(센서가 normal에 고정)와 무관하다 — 비전이 천장을 받치고(0~2), 센서가 위험을 연다(3).

### 3.4 위험(3) — **활성 권장**
- 이제 **명확한 물리적 의미**가 있다: **센서가 확산성 심각 분진을 확인(dust 상승) + 비전도 강함(score 높음)**
  = 둘 다 동의하는 최심각 상태 → `위험(3)`.
- §3.3 매트릭스 우하단(`dust 상승 × score 높음`)이 그 셀. → **LOAS 4단계(0~3) 완전 충족.**
- decision_agent(3단계)로는 못 내던 등급을, 2입력 + 센서 물리로 **근거 있게** 산출한다.

### 3.5 결측/엣지 처리(설계 규칙)
| 상황 | 처리(설계) |
|---|---|
| `score` 없음(REST 미수신/데모) | `score_band=낮음`으로 간주 → 사실상 dust 단독 판정(보수적). 또는 `event_id=NULL`로 "미판정" 표기(택1, §7-4) |
| `dust_value` NULL | `dust_band=정상`으로 간주(보수적) |
| 둘 다 없음 | `event_id=0`(또는 NULL) — §7-4에서 확정 |
| score 방향성 | "높을수록 이상" 가정. 반대면 임계 비교 부호만 config로 반전 |

---

## 4. 단위(granularity) 정합

```
dust_inspection (측정별, fine)            ┐
   각 측정행: 자기 dust_value 사용         ├─► event_id = MATRIX(dust_value, score)
transfer_result.score (waypoint별, coarse)┘   (같은 waypoint의 모든 측정행에 score broadcast)
```
- LOAS 1행 = dust 측정 1건. 그 행의 `dust_value` + **소속 waypoint의 `score`(broadcast)** 로 event_id 산출.
- 한 waypoint에 측정 N건이면 → score는 공유, dust_value는 행마다 다름 → event_id는 행마다 다를 수 있음(정상 동작).

---

## 5. 결합(조립) 설계 — LOAS 1행 구성

```
LOAS MariaDB 1행 (per dust 측정)
 ├─ [24컬럼] dust_inspection (anchor)                  : 측정값·좌표·자세·라우팅·식별자
 ├─ [event_id] = MATRIX(dust_value, score)             : §3 규칙
 └─ [image_data] = Base64(read(REST.image_path))        : REST 결과 이미지(waypoint broadcast)
```

### 5.1 조인 키 (gateway_db 내부에서 완결)
| 결합 | 키 | 비고 |
|---|---|---|
| dust_inspection ⋈ transfer_result(score, image_path) | `waypoint_id` (+ 시간창) | ⚠️ amr_id(transfer_result) ↔ ugv_id(dust_inspection) 직접 동일 컬럼 아님 → **waypoint_id + 시간창** 또는 `cctv_frame.dust_inspection_id` 경유 매핑 필요(§7-3) |

- **decision_db 조인 불필요**(D1 효과). 모든 입력이 gateway_db에 있음.

### 5.2 조립 위치 (권장: 단일 export 소스)
- gateway_db에 **`loas_export` 뷰/적재테이블**을 두어 `dust_inspection ⋈ transfer_result` +
  event_id 계산을 모으고, **egress(또는 적재기)는 이 한 소스만 읽어 MariaDB upsert**.
- event_id 계산을 **(a) 뷰/SQL 내부**(매트릭스를 매핑테이블로) 또는 **(b) 적재기 코드**에서 수행 —
  설정 가능성·테스트성 고려해 결정(§7-5). 본 문서는 규칙만 정의(구현 아님).

---

## 6. 설정(config) 파라미터 명세 (설계)

```yaml
event_id:
  enabled_levels: [0, 1, 2, 3]       # 위험(3) 활성(§3.4)
  score_direction: higher_is_worse   # 반대면 lower_is_worse
  dust_thresholds:  [T_dust]         # 길이 = dust_band 수 - 1. T_dust = 확산 시작 임계(정상 baseline 바로 위)
  score_thresholds: [T_s1, T_s2]     # 길이 = score_band 수 - 1
  matrix:                            # [dust_band][score_band] → event_id (§3.3)
    - [0, 1, 2]                      # dust 정상  (행=dust_band 오름차순)
    - [2, 2, 3]                      # dust 상승  (열=score_band 오름차순)
  on_missing_score: assume_low       # assume_low | null
  on_missing_dust:  assume_low
```
- `matrix` 차원 = `len(dust_thresholds)+1` × `len(score_thresholds)+1` (검증 규칙).
- 임계/매트릭스만 바꾸면 정책 변경(코드 무수정). A/B 철학(§3.3)도 matrix 셀로 표현.

---

## 7. 확정 필요 항목 (설계상 미결정)

1. **decision_agent 우회 합의(D2)** — LOAS event_id를 decision_agent 없이 산출하는 것에 대한 공식 합의. (final_decision이 다른 소비자에 쓰이면 권위 이원화 주의.)
2. **`score` 의미·정규화 & `T_dust` 보정** — score가 어떤 이상을 점수화하는지/범위. `dust_value`는
   "확산 시 상승"이 확인됨(도메인) → **`T_dust`를 정상 baseline 바로 위(확산 시작점)로 보정**해야 함.
   센서 범위가 좁아 절대 상승폭이 작으므로 **임계는 민감하게**(작은 상승도 포착) 잡되, 노이즈와 구분.
3. **score ↔ dust 측정 조인 키** — `waypoint_id`+시간창으로 충분한지, `amr_id↔ugv_id` 대응 또는 `cctv_frame.dust_inspection_id` 경유가 필요한지.
4. **결측 정책** — score/dust 없을 때 `event_id` 보수적 0 vs `NULL`(미판정).
5. **event_id 계산 위치** — `loas_export` 뷰(SQL) vs 적재기 코드.
6. **위험(3) 사용 여부**(D5) 및 임계.
7. **임계 보정(calibration)** — 라벨된 실측으로 `T_dust/T_s1/T_s2` 튜닝 → 혼동행렬 검증. 보정 전엔 보수적 기본값.

---

## 8. 요약

- `event_id = MATRIX(dust_band(dust_value), score_band(score))` — **설정 가능한 2D 밴드 매트릭스**.
- **비전(score)으로 탐지(고민감, 0~2), 센서(dust_value) 상승으로 심각도 상향(고특이도, 위험 3)** —
  탐지=OR, 위험=AND. 센서가 "확산 시 상승"하므로 위험(3)에 물리적 근거가 생김.
- LOAS 행 = **dust 측정 anchor** + event_id + image_data(Base64), **모두 gateway_db 내에서 결합**
  (decision_db 불필요).
- 임계·매트릭스·결측정책·위험3은 **config**로 분리, **실측 보정** 전제.
- 본 문서는 **설계만** 정의하며, 뷰/코드 구현은 §7 확정 후 진행한다.
