"""DUST x,y,z 중간처리 ↔ CCTV 저장 게이팅을 위한 **모듈 전역 공유 상태**.

LOAS 에서 DUST(분진)와 CCTV(영상)는 서로 다른 TCP 연결로 독립적으로 도착한다.
"AMR 이 관측/촬영 중일 때만 수집"하는 결정은 DUST 핸들러에서 내려지지만, 그 결정으로
**영상 파일 저장 여부**도 함께 제어하고 싶다.  두 핸들러가 같은 모듈 전역변수를
읽고 쓰는 방식으로 공유한다.

사용 (반드시 모듈 속성으로 접근 — 재바인딩 주의):
    from . import waypoint_gate as gate
    gate.allow_save = (...)
    if not gate.allow_save: ...

주의(의도된 단순화):
    DUST/CCTV 가 비동기로 도착하므로 "가장 최근 DUST 결정"을 보는 coarse 게이트다
    (엄밀한 프레임 1:1 매칭 아님).  DUST 중간처리 블럭이 **활성(if True)** 이라
    주행 중(waypoint_id=NULL)이면 ``allow_save`` 가 False 가 되어 그 구간 CCTV 는
    저장되지 않는다.  단일 프로세스(asyncio 단일 이벤트루프) 내 공유이므로 락은 불필요.
"""

from __future__ import annotations

from typing import Any

# CCTV 프레임을 저장해도 되는지 (가장 최근 DUST 결정).
# 기동 초기 기본값 True; DUST 중간처리 블럭(활성)이 매 프레임 갱신한다
# (관측/촬영 중 True, 주행 중 False).
allow_save: bool = True

# 가장 최근 DUST 의 target_id (CCTV 파일명용).  CCTV 프레임 자체엔 target_id 가
# 없으므로, 관측 구간에 저장되는 영상 파일명에 쓸 target_id 를 여기서 공유한다.
# 아직 DUST 를 못 봤거나 값이 없으면 None → 파일명은 'NA_...' 로 시작한다.
target_id: int | None = None


# =====================================================================
# 수집 규칙 — "AMR 관측/촬영 중일 때만 수집, 주행 중엔 스킵"
# ---------------------------------------------------------------------
# 현재 정확 조건(LOAS):  waypoint_id(!=NULL,!=0) AND target_id(!=NULL,!=0) → 감지/촬영(수집)
#                       하나라도 NULL/0(주행·미지정) → 스킵
# ★규칙이 바뀌면 should_collect() 만 수정한다(핸들러 불변).
#   cur/prev 는 payload 전체를 받으므로 어떤 컬럼이든 쓸 수 있다(컬럼 미확정 대비).
#   아래 동적 판정 헬퍼(_stationary_key/_close/TOLERANCE)는 이력/좌표 기반 규칙으로
#   되돌릴 때 재사용하도록 그대로 남겨둔다(현재 규칙에선 미사용).
# =====================================================================

def should_collect(cur: Any, prev: Any | None = None) -> bool:
    """관측/촬영 중이면 True(수집), 주행 중이면 False(스킵).

    현재 규칙: **waypoint_id 와 target_id 가 모두 유효(NULL 아님 + 0 아님)** 할 때만 수집.
    - waypoint_id: None(주행) 또는 0(미지정/무효) → 스킵
    - target_id:   None 또는 0 (관측 개소 기준값 없음 → 판정/적재 의미 없음) → 스킵
    cur/prev 는 parse_dust_inspection() 결과 payload **전체**(어떤 컬럼을 쓸지 유동적).
    아래 ``if False:`` 블럭은 **예전 좌표/자세 비교 방식**(prev↔cur)을 보존한 것으로,
    향후 그 규칙으로 되돌릴 때 ``False`` 를 ``True`` 로만 바꾸면 된다.
    """
    # ──[예전 방식 — 비활성(if False)]──────────────────────────────────────
    # 좌표/자세 6값(waypoint_x, y, z, inspection_pan, tilt, lift)을 직전 프레임(prev)과
    # 비교해 **모두 동일**(허용오차 TOLERANCE 이내)이면 = 정지/관측 중 → 수집(True),
    # 하나라도 다르면 = 이동 중 → 스킵(False).  prev 가 없으면(첫 프레임) 비교 불가 → False.
    # 비교 대상 필드는 _stationary_key() 한 곳에서 정의한다(현재 6값).
    # ★되살리려면 아래 `if False:` 를 `if True:` 로 바꾸면 이 블럭이 판정을 담당한다
    #   (그러면 아래 현재 규칙 return 은 도달하지 않는다).
    if False:
        if prev is None:
            return False  # 첫 프레임은 직{"demo": true, "target_id": 1102, "payload": {"amr_id": "amr-01", "target_id": 1102, "frames": [{"received_time": "20260625170858_213", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170858_213.jpg"}, {"received_time": "20260625170858_718", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170858_718.jpg"}, {"received_time": "20260625170859_118", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170859_118.jpg"}, {"received_time": "20260625170859_463", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170859_463.jpg"}, {"received_time": "20260625170859_913", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170859_913.jpg"}, {"received_time": "20260625170900_399", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170900_399.jpg"}, {"received_time": "20260625170900_859", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170900_859.jpg"}, {"received_time": "20260625170901_282", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170901_282.jpg"}, {"received_time": "20260625170901_744", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170901_744.jpg"}, {"received_time": "20260625170902_164", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170902_164.jpg"}, {"received_time": "20260625170902_626", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170902_626.jpg"}, {"received_time": "20260625170903_083", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170903_083.jpg"}, {"received_time": "20260625170903_571", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170903_571.jpg"}, {"received_time": "20260625170903_989", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170903_989.jpg"}, {"received_time": "20260625170904_412", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170904_412.jpg"}, {"received_time": "20260625170904_926", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170904_926.jpg"}, {"received_time": "20260625170905_365", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170905_365.jpg"}, {"received_time": "20260625170905_799", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170905_799.jpg"}, {"received_time": "20260625170906_241", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170906_241.jpg"}, {"received_time": "20260625170906_673", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170906_673.jpg"}, {"received_time": "20260625170907_132", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170907_132.jpg"}, {"received_time": "20260625170907_580", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170907_580.jpg"}, {"received_time": "20260625170908_022", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170908_022.jpg"}, {"received_time": "20260625170908_466", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170908_466.jpg"}, {"received_time": "20260625170908_948", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170908_948.jpg"}, {"received_time": "20260625170909_387", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170909_387.jpg"}, {"received_time": "20260625170909_795", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170909_795.jpg"}, {"received_time": "20260625170910_252", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170910_252.jpg"}, {"received_time": "20260625170910_726", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170910_726.jpg"}, {"received_time": "20260625170911_208", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170911_208.jpg"}, {"received_time": "20260625170911_632", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170911_632.jpg"}, {"received_time": "20260625170912_120", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170912_120.jpg"}, {"received_time": "20260625170912_607", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170912_607.jpg"}, {"received_time": "20260625170912_992", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170912_992.jpg"}, {"received_time": "20260625170913_456", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170913_456.jpg"}, {"received_time": "20260625170913_937", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170913_937.jpg"}, {"received_time": "20260625170914_345", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170914_345.jpg"}, {"received_time": "20260625170914_820", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170914_820.jpg"}, {"received_time": "20260625170915_273", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170915_273.jpg"}, {"received_time": "20260625170915_711", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170915_711.jpg"}, {"received_time": "20260625170916_118", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170916_118.jpg"}, {"received_time": "20260625170916_572", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170916_572.jpg"}, {"received_time": "20260625170916_973", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170916_973.jpg"}, {"received_time": "20260625170917_489", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170917_489.jpg"}, {"received_time": "20260625170917_873", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170917_873.jpg"}, {"received_time": "20260625170918_428", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170918_428.jpg"}, {"received_time": "20260625170918_809", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170918_809.jpg"}, {"received_time": "20260625170919_329", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170919_329.jpg"}, {"received_time": "20260625170919_730", "file_path": "/home/daim/svc/SocketDaim/storage/cctv/amr-01/2026-06-25/17/1102_20260625170919_730.jpg"}]}, "event": "rest_api_request", "level": "info", "timestamp": "2026-06-25T17:09:29.018248+09:00"}
{"demo": true, "target_id": 1102, "status": 200, "body": [{"score": 0.5, "path1": "/data/storage/cctv/amr-01/2026-06-25/17/1102_20260625170858_213.jpg", "path2": "/data/storage/cctv/amr-01/2026-06-25/17/1102_20260625170858_213.jpg"}, {"score": 0.5, "path1": "/data/storage/cctv/amr-01/2026-06-25/17/1102_20260625170858_213.jpg", "path2": "/data/storage/cctv/amr-01/2026-06-25/17/1102_20260625170858_213.jpg"}], "event": "rest_api_response", "level": "info", "timestamp": "2026-06-25T17:09:29.018442+09:00"}전 비교 불가 → 스킵
        return all(
            _close(a, b)
            for a, b in zip(_stationary_key(cur), _stationary_key(prev))
        )
    # ──[현재 규칙 — 활성]──────────────────────────────────────────────────
    # waypoint_id·target_id 모두 유효(!=NULL, !=0)일 때만 수집.
    return (
        cur.waypoint_id is not None and cur.waypoint_id != 0
        and cur.target_id is not None and cur.target_id != 0
    )


# ---------------------------------------------------------------------
# (재사용 대비) 동적 판정 헬퍼 — 직전 payload 와 좌표/자세 비교 기반 규칙용.
# should_collect() 안의 `if False:` 블럭이 이 _stationary_key()/_close()/TOLERANCE 를
# 사용한다(현재 비활성).  되살리려면 그 블럭의 `if False:` → `if True:` 로 바꾸면 된다.
# ---------------------------------------------------------------------

# 좌표 허용오차: 정지여도 미세 jitter 가 있을 수 있어 |Δ| <= TOLERANCE 면 "동일(정지)"로 본다.
TOLERANCE: float = 0.0


def _close(a: Any, b: Any) -> bool:
    """값 하나가 허용오차 이내로 같은가.  None 은 둘 다 None 일 때만 같음.
    숫자가 아니면(문자열 등) 정확 일치로 비교."""
    if a is None or b is None:
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= TOLERANCE
    return a == b


def _stationary_key(payload: Any) -> tuple[Any, ...]:
    """정지 판정에 사용할 값들의 튜플.  어떤 필드를 볼지는 여기만 수정.
    현재: 위치 좌표(waypoint_x, y, z) + 자세(inspection_pan, tilt, lift) = 6값.
    좌표 float 는 jitter 대비 TOLERANCE 비교, pan/tilt/lift int 는 사실상 정확 일치.
    필드를 늘리거나 줄이려면(속도/식별자 등) 이 튜플만 바꾸면 should_collect 의
    `if False:` 블럭이 그대로 따라간다."""
    return (
        payload.waypoint_x, payload.waypoint_y, payload.waypoint_z,
        payload.inspection_pan, payload.inspection_tilt, payload.inspection_lift,
    )
