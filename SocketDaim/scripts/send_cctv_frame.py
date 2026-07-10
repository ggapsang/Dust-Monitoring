#!/usr/bin/env python3
"""LOAS Tfoi v4a CCTV 프레임 1개를 13320 으로 송신하는 로컬 테스트 도구.

와이어 포맷 (ingestion_gateway 가 기대하는 그대로):
    [ 5바이트 ASCII 해상도태그 ][ uint32 빅엔디안 본문길이 ][ 본문(JPEG) ]
    프레임당 연결 1개: connect -> 헤더 -> 본문 -> close

사용:
    python3 send_cctv_frame.py [HOST] [PORT] [BODY_BYTES] [--zero-len] [--truncate N]

    HOST        기본 127.0.0.1
    PORT        기본 13320
    BODY_BYTES  본문 크기(바이트), 기본 4000 (MTU 1500 초과 → 다중 세그먼트)
    --zero-len  헤더 길이필드를 0 으로 보냄(원격 버그 재현용)
    --truncate N  헤더엔 정상 길이를 쓰되 본문은 N 바이트만 보내고 끊음(잘림 재현)
"""
import socket
import struct
import sys

HOST = "127.0.0.1"
PORT = 13320
BODY = 4000
zero_len = "--zero-len" in sys.argv
truncate = None

pos = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(pos) >= 1: HOST = pos[0]
if len(pos) >= 2: PORT = int(pos[1])
if len(pos) >= 3: BODY = int(pos[2])
if "--truncate" in sys.argv:
    truncate = int(sys.argv[sys.argv.index("--truncate") + 1])

# 최소 JPEG 매직(FFD8 ... FFD9). 게이트웨이는 내용 검증은 안 하지만 현실감을 위해.
body = b"\xff\xd8\xff\xe0" + b"X" * (BODY - 6) + b"\xff\xd9"
tag = b"V640p"                       # 유효한 5바이트 해상도 태그
declared = 0 if zero_len else len(body)
header = tag + struct.pack("!I", declared)   # !I = 빅엔디안 uint32

send_body = body if truncate is None else body[:truncate]

with socket.create_connection((HOST, PORT), timeout=5) as s:
    s.sendall(header)
    s.sendall(send_body)
    s.shutdown(socket.SHUT_WR)       # close (one frame per connection)
print(f"sent -> {HOST}:{PORT}  tag=V640p declared_len={declared} "
      f"body_sent={len(send_body)} (header 9B + body {len(send_body)}B)")
