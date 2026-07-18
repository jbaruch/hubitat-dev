#!/usr/bin/env python3
"""Tail a Hubitat hub's live log or event websocket, filtered, as structured lines.

The hub exposes ws://<ip>/logsocket (logs) and ws://<ip>/eventsocket (device events) —
undocumented, no auth on a local hub with Hub Security off, server frames are unmasked
text (see ../_reference/endpoints.md, grounded on 2.5.1.125). No external library: a minimal
stdlib websocket client does the handshake and frame parsing.

The deterministic pieces — handshake bytes, frame parsing, the filter predicate, line
formatting — are pure functions and unit-tested; only the socket loop touches the network.

Usage:
    hub_logtail.py --ip 192.0.2.10 [--socket logsocket|eventsocket]
                   [--levels error,warn,info] [--type dev|app] [--name SUBSTR] [--id N]
                   [--seconds 30] [--max-frames N] [--follow] [--json]
Default: 30 seconds of the log socket, formatted lines. --follow runs until interrupted.
"""
import argparse
import base64
import json
import os
import socket
import sys
import time
from typing import Optional

_LEVEL_ORDER = ["error", "warn", "info", "debug", "trace"]


def build_handshake(host: str, path: str, key_bytes: Optional[bytes] = None):
    """Return (request_bytes, sec_websocket_key). key_bytes lets tests pin the key."""
    key = base64.b64encode(key_bytes if key_bytes is not None else os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    return req.encode(), key


def iter_frames(buf: bytearray):
    """Pop complete websocket frames from buf (mutated in place). Yields (opcode, payload_bytes)
    for each complete frame; leaves any partial trailing frame in buf. Handles server→client
    (unmasked) and, defensively, masked frames."""
    while True:
        if len(buf) < 2:
            return
        b0, b1 = buf[0], buf[1]
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        length = b1 & 0x7F
        offset = 2
        if length == 126:
            if len(buf) < 4:
                return
            length = int.from_bytes(buf[2:4], "big")
            offset = 4
        elif length == 127:
            if len(buf) < 10:
                return
            length = int.from_bytes(buf[2:10], "big")
            offset = 10
        mask = b""
        if masked:
            if len(buf) < offset + 4:
                return
            mask = buf[offset:offset + 4]
            offset += 4
        if len(buf) < offset + length:
            return
        payload = bytes(buf[offset:offset + length])
        if masked:
            payload = bytes(p ^ mask[i % 4] for i, p in enumerate(payload))
        del buf[:offset + length]
        yield opcode, payload


def matches(frame: dict, levels=None, type_=None, name_substr=None, dev_id=None) -> bool:
    """Pure filter predicate over a decoded frame dict."""
    if levels and frame.get("level") not in levels:
        return False
    if type_ and frame.get("type") != type_:
        return False
    if name_substr and name_substr.lower() not in (frame.get("name") or "").lower():
        return False
    if dev_id is not None and str(frame.get("id")) != str(dev_id):
        return False
    return True


def format_frame(frame: dict, as_json: bool = False) -> str:
    if as_json:
        return json.dumps(frame, sort_keys=True)
    t = frame.get("time", "")
    level = (frame.get("level") or "").upper()
    name = frame.get("name", "")
    msg = frame.get("msg", frame.get("descriptionText", ""))
    return f"{t} [{level:<5}] {name}: {msg}"


def expand_levels(levels_arg, min_level):
    if levels_arg:
        return set(x.strip() for x in levels_arg.split(",") if x.strip())
    if min_level:
        i = _LEVEL_ORDER.index(min_level)
        return set(_LEVEL_ORDER[:i + 1])
    return None


def _stream(sock, buf, filters, as_json, deadline, max_frames, follow, out):
    count = 0
    while True:
        if not follow and deadline is not None and time.monotonic() >= deadline:
            return count
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            return count
        buf.extend(chunk)
        for opcode, payload in iter_frames(buf):
            if opcode == 0x8:  # close
                return count
            if opcode not in (0x1, 0x2):  # ignore ping/pong/continuation
                continue
            try:
                frame = json.loads(payload.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            if matches(frame, **filters):
                out.write(format_frame(frame, as_json) + "\n")
                out.flush()
                count += 1
                if max_frames and count >= max_frames:
                    return count


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Tail a Hubitat log/event websocket.")
    p.add_argument("--ip", required=True)
    p.add_argument("--socket", default="logsocket", choices=["logsocket", "eventsocket"])
    p.add_argument("--levels", help="comma list e.g. error,warn,info")
    p.add_argument("--min-level", choices=_LEVEL_ORDER, help="include this level and more severe")
    p.add_argument("--type", choices=["dev", "app"])
    p.add_argument("--name", help="substring match on the source name")
    p.add_argument("--id", type=int, help="match this device/app id")
    p.add_argument("--seconds", type=int, default=30)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--follow", action="store_true", help="run until interrupted")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    filters = {"levels": expand_levels(args.levels, args.min_level),
               "type_": args.type, "name_substr": args.name, "dev_id": args.id}
    request, _ = build_handshake(args.ip, f"/{args.socket}")

    try:
        sock = socket.create_connection((args.ip, 80), timeout=10)
    except OSError as e:
        print(f"cannot connect to {args.ip}:80 — {e}", file=sys.stderr)
        return 1
    try:
        sock.sendall(request)
        sock.settimeout(5.0)
        # Read until the header terminator — a TCP read can return the handshake
        # response in several packets, so a single recv() may hold only part of it.
        raw = b""
        while b"\r\n\r\n" not in raw and len(raw) < 8192:
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        sock.settimeout(1.0)
        sep = raw.find(b"\r\n\r\n")
        header = (raw[:sep] if sep >= 0 else raw).decode("latin1", "replace")
        if "101" not in header.split("\r\n", 1)[0]:
            print(f"websocket handshake failed: {header.splitlines()[0] if header else '(no response)'}",
                  file=sys.stderr)
            return 1
        # bytes already read past the header terminator are the start of the frame stream
        buf = bytearray(raw[sep + 4:]) if sep >= 0 else bytearray()
        deadline = None if args.follow else time.monotonic() + args.seconds
        _stream(sock, buf, filters, args.json, deadline, args.max_frames, args.follow, sys.stdout)
    except KeyboardInterrupt:
        return 0
    except OSError as e:
        print(f"log-tail connection to {args.ip} failed: {e}", file=sys.stderr)
        return 1
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
