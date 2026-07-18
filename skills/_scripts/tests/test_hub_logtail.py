#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_logtail.py — websocket frame parsing, filtering, formatting."""

import importlib.util
import io
import json
import socket
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_logtail.py"
spec = importlib.util.spec_from_file_location("hub_logtail", SCRIPT)
assert spec and spec.loader
lt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lt)


def server_text_frame(text: str) -> bytes:
    payload = text.encode()
    b = bytearray([0x81])  # FIN + text opcode
    n = len(payload)
    if n < 126:
        b.append(n)
    elif n < 65536:
        b.append(126)
        b += n.to_bytes(2, "big")
    else:
        b.append(127)
        b += n.to_bytes(8, "big")
    b += payload
    return bytes(b)


LOG = {"name": "Kitchen Motion", "msg": "active", "id": 12, "time": "2026-07-14 08:00:00.0",
       "type": "dev", "level": "info"}


class TestIterFrames(unittest.TestCase):
    def test_single_frame(self):
        buf = bytearray(server_text_frame("hello"))
        frames = list(lt.iter_frames(buf))
        self.assertEqual(frames, [(0x1, b"hello")])
        self.assertEqual(len(buf), 0)

    def test_multiple_frames_in_one_buffer(self):
        buf = bytearray(server_text_frame("a") + server_text_frame("bb"))
        payloads = [p for _, p in lt.iter_frames(buf)]
        self.assertEqual(payloads, [b"a", b"bb"])

    def test_partial_frame_left_in_buffer(self):
        full = server_text_frame("abcdef")
        buf = bytearray(full[:4])  # header + partial payload
        self.assertEqual(list(lt.iter_frames(buf)), [])
        self.assertEqual(bytes(buf), full[:4])  # untouched, awaiting more bytes
        buf.extend(full[4:])
        self.assertEqual([p for _, p in lt.iter_frames(buf)], [b"abcdef"])

    def test_extended_length_126(self):
        text = "x" * 200
        buf = bytearray(server_text_frame(text))
        self.assertEqual([p for _, p in lt.iter_frames(buf)], [text.encode()])

    def test_close_opcode_surfaced(self):
        buf = bytearray([0x88, 0x00])  # FIN + close, no payload
        self.assertEqual(list(lt.iter_frames(buf)), [(0x8, b"")])


class TestMatches(unittest.TestCase):
    def test_level_filter(self):
        self.assertTrue(lt.matches(LOG, levels={"info", "warn"}))
        self.assertFalse(lt.matches(LOG, levels={"error"}))

    def test_type_filter(self):
        self.assertTrue(lt.matches(LOG, type_="dev"))
        self.assertFalse(lt.matches(LOG, type_="app"))

    def test_name_substring_case_insensitive(self):
        self.assertTrue(lt.matches(LOG, name_substr="kitchen"))
        self.assertFalse(lt.matches(LOG, name_substr="garage"))

    def test_id_filter(self):
        self.assertTrue(lt.matches(LOG, dev_id=12))
        self.assertFalse(lt.matches(LOG, dev_id=99))

    def test_no_filters_matches_all(self):
        self.assertTrue(lt.matches(LOG))


class TestFormatAndLevels(unittest.TestCase):
    def test_format_human(self):
        line = lt.format_frame(LOG)
        self.assertIn("[INFO ]", line)
        self.assertIn("Kitchen Motion: active", line)

    def test_format_json_roundtrips(self):
        self.assertEqual(json.loads(lt.format_frame(LOG, as_json=True)), LOG)

    def test_expand_min_level(self):
        self.assertEqual(lt.expand_levels(None, "warn"), {"error", "warn"})

    def test_expand_explicit_levels(self):
        self.assertEqual(lt.expand_levels("error,info", None), {"error", "info"})

    def test_expand_none(self):
        self.assertIsNone(lt.expand_levels(None, None))


class FakeSocket:
    """Yields queued byte chunks from recv, then simulates close with b''. A None entry
    raises socket.timeout (to exercise the timeout-continue path)."""
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def recv(self, _n):
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if chunk is None:
            raise socket.timeout()
        return chunk


class TestStream(unittest.TestCase):
    def test_filters_and_writes_matching_frames(self):
        frames = (server_text_frame(json.dumps(LOG))
                  + server_text_frame(json.dumps({**LOG, "level": "debug", "msg": "noisy"})))
        out = io.StringIO()
        sock = FakeSocket([frames, b""])
        count = lt._stream(sock, bytearray(), {"levels": {"info"}, "type_": None,
                           "name_substr": None, "dev_id": None},
                           False, None, 0, False, out)
        self.assertEqual(count, 1)
        self.assertIn("active", out.getvalue())
        self.assertNotIn("noisy", out.getvalue())

    def test_max_frames_stops_early(self):
        many = b"".join(server_text_frame(json.dumps(LOG)) for _ in range(5))
        out = io.StringIO()
        count = lt._stream(FakeSocket([many]), bytearray(), {"levels": None, "type_": None,
                           "name_substr": None, "dev_id": None},
                           False, None, 2, False, out)
        self.assertEqual(count, 2)

    def test_timeout_chunk_is_tolerated(self):
        out = io.StringIO()
        sock = FakeSocket([None, server_text_frame(json.dumps(LOG)), b""])
        count = lt._stream(sock, bytearray(), {"levels": None, "type_": None,
                           "name_substr": None, "dev_id": None},
                           False, None, 0, False, out)
        self.assertEqual(count, 1)

    def test_non_timeout_socket_error_propagates(self):
        # A hard socket error (not a timeout) must propagate out of _stream so the CLI
        # layer reports it as a clean error rather than continuing the loop.
        class Broken:
            def recv(self, _n):
                raise OSError("connection reset")
        with self.assertRaises(OSError):
            lt._stream(Broken(), bytearray(), {"levels": None, "type_": None,
                       "name_substr": None, "dev_id": None}, False, None, 0, False, io.StringIO())


if __name__ == "__main__":
    unittest.main()
