#!/usr/bin/env python3
"""LOAS Tfoi v4a — AMR 순회 관측 **시나리오** 송출기 (DUST 13310 / CCTV 13320).

데이터 필드/값은 명세서 ``LOAS_ARQOS_분진센서_데이터_명세서_v0.1.xlsx`` 의
"3.데이터 매핑정보"(DB Column ↔ XML Tag) / "4. Sample Query" 를 기준으로 한다.
  · inspection_value 의 원천 태그는 <DUST_DATA>(분진 측정값).
  · sensor_index=13(분진센서), sensor_type=1, event_id/image_data 는 센서 미전송.
  · inspection_local_id = "검사 요청 고유 ID" → 목표 방문마다 유니크.

실제 AMR 동작 모사:
  · CCTV 카메라는 상태와 무관하게 2 Hz 연속 송신(서버 게이트가 저장 여부 결정).
  · DUST 센서는 1 Hz. 이동 중 waypoint_id=NULL(태그 생략)+target_id=다음목표,
    정지 관측 중 waypoint_id!=NULL+target_id=목표값.

타임라인(기본): 최초 2초 주행 → [관측 6초 + 이동 2초]×4목표×3회 = 98초.
(관측 6초 ≥ PoolerTran 폴링 5초 → 모든 waypoint 가 한 번 이상 '현재'로 잡혀 누락 없음.)

서버측 효과(waypoint_gate):
  · 이동 DUST(waypoint_id=NULL) → 미저장 + allow_save=False → 그 구간 CCTV 미저장.
  · 관측 DUST → 저장 + allow_save=True → 관측 구간 CCTV 저장 → Correlator 페어링.

사용:
  python3 send_loas_scenario.py            # 62초 실행
  python3 send_loas_scenario.py --dry-run  # 타임라인/예상 건수만
  python3 send_loas_scenario.py --speed 5  # 5배속(빠른 검증)
"""
from __future__ import annotations

import argparse
import random
import socket
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# -- LOAS 상수 (gw_proto.codec.loas.constants 와 동일) ----------------------
SOP_DUST = 0xAABB
DOID_DUST_INSPECTION = 0xD002
PROTOCOL_VERSION = 0x02
DUST_MAX_BODY = 1448
_DUST_HEADER = struct.Struct("<HHBBIH")   # 리틀엔디안(실측)
_CCTV_LEN = struct.Struct("!I")           # CCTV 길이는 빅엔디안
CCTV_TAGS = {"V1080", "V720p", "V640p"}

# 4개 목표 메타데이터 — 목표마다 좌표/객체ID/분진기준이 다르다(현실성).
#   target_id / waypoint_id 는 사용자 시나리오 규칙(100→101, 200→201, …).
#   wx/wy/wz: 명세서 WAYPOINT_X/Y/Z (ROS Map Frame), object_id: 검사 객체 ID,
#   iot: 그 개소의 분진(DUST_DATA) 값 — **관측 단위 고정**(같은 관측의 모든 프레임 동일).
#        → 대표값(max)도 그 값이 되어 sensor 채널이 개소별로 결정된다.
#   ※ 정적/동적분진(static/dynamic)은 DUST 가 아니라 PoolerTran demo 가 만든다
#     (PoolerTran/src/poolertran/rest_client.py 의 waypoint별 프로필 참조).
TARGETS = [
    {"target_id": 100, "waypoint_id": 101, "wx": 12.345, "wy": 67.890, "wz": 0.0, "object_id": 1, "iot": 2.5},
    {"target_id": 200, "waypoint_id": 201, "wx": 23.100, "wy": 45.600, "wz": 0.0, "object_id": 2, "iot": 0.5},
    {"target_id": 300, "waypoint_id": 301, "wx": 34.250, "wy": 12.780, "wz": 0.0, "object_id": 3, "iot": 2.5},
    {"target_id": 400, "waypoint_id": 401, "wx": 41.900, "wy": 88.300, "wz": 0.0, "object_id": 4, "iot": 0.5},
]
LOCAL_ID_BASE = 1000001   # inspection_local_id 시작값 (검사 요청 고유 ID)


# ---------------------------------------------------------------------------
# 프레임 빌더
# ---------------------------------------------------------------------------
def build_dust_frame(*, tgt: dict, waypoint_id: int | None,
                     inspection_local_id: int | None, dust_value: float,
                     exec_id: int, ts_epoch: int) -> bytes:
    """DUST_INSPECTION_INFOR XML 1건 → 12B 헤더 + 본문 (명세서 매핑 기준).

    waypoint_id 가 None 이면 <WAYPOINT_ID> 태그를 생략(=NULL, 주행 상태).
    값이 있으면 태그 포함(=관측 상태).
    """
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
    wp_tag = "" if waypoint_id is None else f"<WAYPOINT_ID>{waypoint_id}</WAYPOINT_ID>"
    local_id = 0 if inspection_local_id is None else inspection_local_id
    body_xml = (
        "<ELEMENT>"
        "<CMD_ID>DUST_INSPECTION_INFOR</CMD_ID>"
        f"<DATETIME>{now_iso}</DATETIME>"
        f"<DUST_DATA>{dust_value}</DUST_DATA>"          # → inspection_value (분진 측정값)
        "<DUST_ALARM>3</DUST_ALARM>"                    # gateway 내부(t_inspection 미매핑)
        "<SENSOR_TYPE>1</SENSOR_TYPE>"
        "<SENSOR_INDEX>13</SENSOR_INDEX>"               # 분진센서 = 13 (명세)
        "<TARGET_INDEX>1</TARGET_INDEX>"                # Sample Query 기준(매핑시트엔 없음)
        f"<WAYPOINT_X>{tgt['wx']}</WAYPOINT_X>"
        f"<WAYPOINT_Y>{tgt['wy']}</WAYPOINT_Y>"
        f"<WAYPOINT_Z>{tgt['wz']}</WAYPOINT_Z>"
        "<ROT_X>0.0</ROT_X><ROT_Y>0.0</ROT_Y><ROT_Z>0.0</ROT_Z><ROT_W>1.0</ROT_W>"
        "<UGV_ID>1</UGV_ID>"
        "<LOCATION_ID>1</LOCATION_ID>"
        "<MAP_ID>1</MAP_ID>"
        "<NAVIGATION_ID>1039</NAVIGATION_ID>"
        f"<EXEC_ID>{exec_id}</EXEC_ID>"
        "<PLANT_ID>1</PLANT_ID>"
        f"<TARGET_ID>{tgt['target_id']}</TARGET_ID>"
        f"{wp_tag}"
        f"<INSPECTION_LOCAL_ID>{local_id}</INSPECTION_LOCAL_ID>"
        f"<OBJECT_ID>{tgt['object_id']}</OBJECT_ID>"
        "<MISSION_ID>1</MISSION_ID>"
        "<INSPECTION_PAN>0</INSPECTION_PAN>"
        "<INSPECTION_TILT>0</INSPECTION_TILT>"
        "<INSPECTION_LIFT>0</INSPECTION_LIFT>"
        "</ELEMENT>"
    )
    body = body_xml.encode("utf-8")
    if len(body) > DUST_MAX_BODY:
        raise SystemExit(f"DUST 본문 {len(body)}B > 최대 {DUST_MAX_BODY}B")
    header = _DUST_HEADER.pack(
        SOP_DUST, DOID_DUST_INSPECTION, PROTOCOL_VERSION, 0, ts_epoch, len(body)
    )
    return header + body


def build_cctv_frame(resolution: str, body: bytes) -> bytes:
    """해상도 태그 + 빅엔디안 길이 + 이미지 본문."""
    return resolution.encode("ascii") + _CCTV_LEN.pack(len(body)) + body


def load_cctv_body(image_path: str, fallback_bytes: int) -> bytes:
    """CCTV 본문 바이트.  image_path 가 있으면 그 파일을 그대로 사용(실제 이미지),
    없으면 합성 JPEG(FFD8..FFD9) 바이트로 폴백."""
    if image_path:
        p = Path(image_path)
        if p.is_file():
            data = p.read_bytes()
            print(f"CCTV 이미지 원본: {image_path} ({len(data)} bytes)")
            return data
        print(f"⚠ CCTV 이미지 {image_path} 없음 → 합성 바이트 사용", file=sys.stderr)
    n = max(6, fallback_bytes)
    return b"\xff\xd8\xff\xe0" + b"X" * (n - 6) + b"\xff\xd9"


# ---------------------------------------------------------------------------
# 시나리오 타임라인
# ---------------------------------------------------------------------------
def build_segments(cycles: int, lead_move: float, observe: float, move: float):
    """구간 = [start, end, state, tgt(dict), waypoint_id, inspection_local_id].

    관측 구간마다 inspection_local_id 를 1씩 증가시켜 "검사 요청 고유 ID" 를 부여.
    이동 구간은 target_id=다음목표, waypoint_id=None, local_id=None(미저장).
    """
    segs: list[list] = []
    t = 0.0
    if lead_move > 0:
        segs.append([t, t + lead_move, "move", TARGETS[0], None, None])
        t += lead_move
    visit = 0
    for _ in range(cycles):
        for i, tgt in enumerate(TARGETS):
            segs.append([t, t + observe, "observe", tgt, tgt["waypoint_id"],
                         LOCAL_ID_BASE + visit])
            t += observe; visit += 1
            nxt = TARGETS[(i + 1) % len(TARGETS)]      # 다음 목표(순환)
            segs.append([t, t + move, "move", nxt, None, None]); t += move
    return segs, t


def phase_at(segs, t: float):
    for s in segs:
        if s[0] <= t < s[1]:
            return s
    return segs[-1]


def build_events(total: float, dust_hz: float, cctv_hz: float):
    """(offset, order, kind) 정렬 이벤트. 동시각이면 DUST(order 0) 먼저."""
    events = []
    for hz, order, kind in ((dust_hz, 0, "dust"), (cctv_hz, 1, "cctv")):
        step = 1.0 / hz
        i = 0
        while i * step < total - 1e-9:
            events.append((round(i * step, 6), order, kind)); i += 1
    events.sort(key=lambda e: (e[0], e[1]))
    return events


# ---------------------------------------------------------------------------
def _send(host: str, port: int, payload: bytes) -> None:
    with socket.create_connection((host, port), timeout=5.0) as s:
        s.sendall(payload)


def print_timeline(segs, total, events):
    n_dust = sum(1 for e in events if e[2] == "dust")
    n_cctv = sum(1 for e in events if e[2] == "cctv")
    print(f"총 길이: {total:.1f}s | 구간 {len(segs)}개 | DUST {n_dust}건(1Hz) CCTV {n_cctv}건(2Hz)")
    print("타임라인:")
    for s in segs:
        wp = "NULL" if s[4] is None else s[4]
        lid = "-" if s[5] is None else s[5]
        print(f"  [{s[0]:5.1f}–{s[1]:5.1f}s] {s[2]:7} target_id={s[3]['target_id']:>3} "
              f"waypoint_id={wp:>5} local_id={lid}")
    obs_sec = sum(s[1] - s[0] for s in segs if s[2] == "observe")
    print(f"\n예상 저장(관측 구간만): DUST≈{int(obs_sec)}건, CCTV≈{int(obs_sec*2)}건 "
          f"(이동 구간 DUST/CCTV 는 게이트로 미저장)")


def main() -> None:
    p = argparse.ArgumentParser(description="LOAS AMR 순회 관측 시나리오 송출기")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--dust-port", type=int, default=13310)
    p.add_argument("--cctv-port", type=int, default=13320)
    p.add_argument("--cycles", type=int, default=3, help="4목표 순회 반복 횟수")
    p.add_argument("--lead-move", type=float, default=2.0, help="최초 주행(초)")
    p.add_argument("--observe", type=float, default=6.0,
                   help="목표당 관측(정지) 시간(초). 기본 6 — PoolerTran 폴링(5초)이 "
                        "모든 waypoint 를 놓치지 않도록 체류>폴링 보장")
    p.add_argument("--move", type=float, default=2.0, help="목표 간 이동(초)")
    p.add_argument("--dust-hz", type=float, default=1.0)
    p.add_argument("--dust-jitter", type=float, default=0.2,
                   help="분진값 지터 폭 ±(중심=개소 iot). 0=고정. "
                        "기본 0.2 → iot 2.5→2.3~2.7, 0.5→0.3~0.7 (임계 2.0 안 넘음)")
    p.add_argument("--cctv-hz", type=float, default=2.0)
    p.add_argument("--exec-id", type=int, default=1001, help="스케줄 실행 ID(이번 run)")
    p.add_argument("--resolution", default="V640p", choices=sorted(CCTV_TAGS))
    p.add_argument("--cctv-image", default="",
                   help="CCTV 본문으로 쓸 실제 이미지 파일 경로. 미지정 시 합성 바이트(기본)")
    p.add_argument("--cctv-bytes", type=int, default=4000,
                   help="합성 본문 크기(bytes) — 기본 방식")
    p.add_argument("--speed", type=float, default=1.0, help="시간 배속(>1 빠르게)")
    p.add_argument("--dry-run", action="store_true", help="송신 없이 타임라인만 출력")
    args = p.parse_args()

    segs, total = build_segments(args.cycles, args.lead_move, args.observe, args.move)
    events = build_events(total, args.dust_hz, args.cctv_hz)
    cctv_body = load_cctv_body(args.cctv_image, args.cctv_bytes)
    print_timeline(segs, total, events)
    if args.dry_run:
        return

    print(f"\n송신 시작 → {args.host} (DUST:{args.dust_port} CCTV:{args.cctv_port}) "
          f"speed×{args.speed}\n")
    start = time.time()
    sent_d = sent_c = 0
    di = 0
    for off, _order, kind in events:
        delay = (start + off / args.speed) - time.time()
        if delay > 0:
            time.sleep(delay)
        try:
            if kind == "dust":
                seg = phase_at(segs, off)
                tgt = seg[3]
                # 분진값(iot): 개소 중심값 ± dust_jitter 균일난수(프레임별).
                # 임계 2.0 을 넘지 않는 폭이라 sensor 판정(개소별)은 그대로 유지되고
                # 측정값만 자연스럽게 분포한다(2.5→2.3~2.7, 0.5→0.3~0.7).
                jit = args.dust_jitter
                dust_value = round(
                    random.uniform(tgt["iot"] - jit, tgt["iot"] + jit), 3
                )
                frame = build_dust_frame(
                    tgt=tgt, waypoint_id=seg[4], inspection_local_id=seg[5],
                    dust_value=dust_value, exec_id=args.exec_id,
                    ts_epoch=int(time.time()),
                )
                _send(args.host, args.dust_port, frame)
                di += 1; sent_d += 1
                wp = "NULL" if seg[4] is None else seg[4]
                print(f"  t={off:5.1f}s DUST  {seg[2]:7} target_id={tgt['target_id']} "
                      f"waypoint_id={wp} dust={dust_value}")
            else:
                _send(args.host, args.cctv_port,
                      build_cctv_frame(args.resolution, cctv_body))
                sent_c += 1
        except OSError as exc:
            print(f"  t={off:5.1f}s {kind} 송신 실패: {exc}", file=sys.stderr)

    print(f"\n완료: DUST {sent_d}건, CCTV {sent_c}건 ({time.time()-start:.1f}s).")
    print("→ ~10초 후 cctv_frame.dust_inspection_id 페어링 확인.")


if __name__ == "__main__":
    main()
