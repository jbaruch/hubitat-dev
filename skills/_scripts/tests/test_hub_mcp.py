#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_mcp.py — the AI (MCP) Connector client.

No live hub and no real network: an injectable transport returns canned JSON-RPC responses,
so the request building, JSON/SSE body parsing, error handling, content unwrap, and session/
token/URL resolution are all exercised deterministically.
"""
import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_mcp.py"
spec = importlib.util.spec_from_file_location("hub_mcp", SCRIPT)
assert spec and spec.loader, f"cannot load hub_mcp.py at {SCRIPT}"
hub_mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub_mcp)
HubError = hub_mcp.HubError


def rpc(req_id, result):
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


class FakeTransport:
    """Records each request and returns whatever `script(message, headers)` yields."""

    def __init__(self, script):
        self.calls = []
        self._script = script

    def __call__(self, http_method, url, body, headers):
        message = json.loads(body) if body else None
        self.calls.append({"method": http_method, "url": url, "message": message, "headers": headers})
        return self._script(message, headers)

    def messages(self, rpc_method):
        return [c["message"] for c in self.calls if (c["message"] or {}).get("method") == rpc_method]


def healthy(message, headers):
    """A well-behaved gateway: initialize (with a session header), the initialized notification,
    a paginated tools/list, and a tools/call that double-encodes its text payload."""
    method = message.get("method")
    if method == "initialize":
        return (200, {"Mcp-Session-Id": "sess-1", "Content-Type": "application/json"},
                rpc(message["id"], {"protocolVersion": "2025-06-18",
                                    "capabilities": {"tools": {"listChanged": False}},
                                    "serverInfo": {"name": "hubitat-mcp-gateway", "version": "0.1.0"},
                                    "instructions": "Prefer read-only tools."}))
    if method == "notifications/initialized":
        return (202, {}, "")
    if method == "tools/list":
        cursor = (message.get("params") or {}).get("cursor")
        if not cursor:
            return (200, {}, rpc(message["id"], {
                "tools": [{"name": "list_modes", "description": "List hub modes and the current mode."}],
                "nextCursor": "page2"}))
        return (200, {}, rpc(message["id"], {
            "tools": [{"name": "get_device", "description": "Get one device.",
                       "inputSchema": {"type": "object", "required": ["deviceId"]}}]}))
    if method == "tools/call":
        name = message["params"]["name"]
        if name == "boom":
            return (200, {}, rpc(message["id"], {"content": [{"type": "text", "text": "device not found"}],
                                                 "isError": True}))
        payload = json.dumps({"currentMode": {"id": 2, "name": "Evening"}})
        return (200, {}, rpc(message["id"], {"content": [{"type": "text", "text": payload}],
                                             "isError": False}))
    return (400, {}, "{}")


def token_file(tmpdir, value="tok-abc"):
    p = Path(tmpdir) / "mcp_token"
    p.write_text(value)
    return str(p)


# ------------------------- pure functions -------------------------

class TestBuildRequest(unittest.TestCase):
    def test_request_carries_id_and_params(self):
        self.assertEqual(hub_mcp.build_request("tools/list", {"cursor": "c"}, 2),
                         {"jsonrpc": "2.0", "method": "tools/list", "id": 2, "params": {"cursor": "c"}})

    def test_notification_omits_id(self):
        msg = hub_mcp.build_request("notifications/initialized", None, req_id=None)
        self.assertEqual(msg, {"jsonrpc": "2.0", "method": "notifications/initialized"})


class TestParseMessageBody(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(hub_mcp.parse_message_body('{"jsonrpc":"2.0","id":1,"result":{}}'),
                         {"jsonrpc": "2.0", "id": 1, "result": {}})

    def test_sse_single_data_frame(self):
        body = "event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"ok\":true}}\n\n"
        self.assertEqual(hub_mcp.parse_message_body(body)["result"], {"ok": True})

    def test_sse_last_data_frame_wins(self):
        body = 'data: {"id":1,"result":{"n":1}}\n\ndata: {"id":1,"result":{"n":2}}\n'
        self.assertEqual(hub_mcp.parse_message_body(body)["result"], {"n": 2})

    def test_empty_body_raises(self):
        with self.assertRaises(HubError):
            hub_mcp.parse_message_body("   ")

    def test_non_json_raises(self):
        with self.assertRaises(HubError):
            hub_mcp.parse_message_body("<html>login</html>")


class TestJsonrpcResult(unittest.TestCase):
    def test_returns_result(self):
        self.assertEqual(hub_mcp.jsonrpc_result({"result": {"a": 1}}), {"a": 1})

    def test_error_object_raises_with_code(self):
        with self.assertRaises(HubError) as ctx:
            hub_mcp.jsonrpc_result({"error": {"code": -32602, "message": "bad args"}})
        self.assertIn("-32602", str(ctx.exception))
        self.assertIn("bad args", str(ctx.exception))

    def test_neither_result_nor_error_raises(self):
        with self.assertRaises(HubError):
            hub_mcp.jsonrpc_result({"jsonrpc": "2.0", "id": 1})


class TestUnwrapToolResult(unittest.TestCase):
    def test_single_json_text_is_parsed(self):
        result = {"content": [{"type": "text", "text": '{"currentMode":{"id":2}}'}], "isError": False}
        out = hub_mcp.unwrap_tool_result(result)
        self.assertEqual(out["data"], {"currentMode": {"id": 2}})
        self.assertFalse(out["isError"])

    def test_plain_text_kept_as_string(self):
        result = {"content": [{"type": "text", "text": "all good"}], "isError": False}
        self.assertEqual(hub_mcp.unwrap_tool_result(result)["data"], "all good")

    def test_is_error_flag_surfaced(self):
        result = {"content": [{"type": "text", "text": "device not found"}], "isError": True}
        self.assertTrue(hub_mcp.unwrap_tool_result(result)["isError"])

    def test_multiple_text_parts_become_list(self):
        result = {"content": [{"type": "text", "text": '{"a":1}'}, {"type": "text", "text": "note"}]}
        self.assertEqual(hub_mcp.unwrap_tool_result(result)["data"], [{"a": 1}, "note"])

    def test_no_text_falls_back_to_structured_content(self):
        result = {"content": [], "structuredContent": {"x": 1}}
        self.assertEqual(hub_mcp.unwrap_tool_result(result)["data"], {"x": 1})


class TestSessionIdFromHeaders(unittest.TestCase):
    def test_case_insensitive(self):
        self.assertEqual(hub_mcp.session_id_from_headers({"mcp-session-id": "s9"}), "s9")
        self.assertEqual(hub_mcp.session_id_from_headers({"Mcp-Session-Id": "s9"}), "s9")

    def test_absent_is_none(self):
        self.assertIsNone(hub_mcp.session_id_from_headers({"Content-Type": "application/json"}))


class TestResolveToken(unittest.TestCase):
    def test_env_wins(self):
        self.assertEqual(hub_mcp.resolve_token(env={"HUBITAT_MCP_TOKEN": " t1 "}), "t1")

    def test_file_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(hub_mcp.resolve_token(env={}, token_file=token_file(d, "filetok")), "filetok")

    def test_empty_file_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(HubError):
                hub_mcp.resolve_token(env={}, token_file=token_file(d, "   "))

    def test_missing_everything_raises_without_leaking(self):
        with self.assertRaises(HubError) as ctx:
            hub_mcp.resolve_token(env={})
        self.assertIn("HUBITAT_MCP_TOKEN", str(ctx.exception))


class TestResolveMcpUrl(unittest.TestCase):
    def test_explicit_url_wins(self):
        self.assertEqual(hub_mcp.resolve_mcp_url(url="http://h/mcp", ip="1.2.3.4"), "http://h/mcp")

    def test_ip_forces_port_80_and_path(self):
        self.assertEqual(hub_mcp.resolve_mcp_url(ip="192.0.2.15"), "http://192.0.2.15/mcp")

    def test_hub_resolves_via_hubs_json_at_port_80(self):
        with tempfile.TemporaryDirectory() as d:
            hubs = Path(d) / "hubs.json"
            hubs.write_text(json.dumps({"schema_version": 1, "default": "main",
                                        "hubs": {"main": {"ip": "192.0.2.9", "port": 8080}}}))
            self.assertEqual(hub_mcp.resolve_mcp_url(hub="main", hubs_path=str(hubs)),
                             "http://192.0.2.9/mcp")

    def test_no_target_raises(self):
        with self.assertRaises(HubError):
            hub_mcp.resolve_mcp_url()


# ------------------------- client -------------------------

class TestClientHandshake(unittest.TestCase):
    def test_initialize_captures_session_and_sends_initialized(self):
        t = FakeTransport(healthy)
        client = hub_mcp.MCPClient("http://h/mcp", "tok", t)
        info = client.initialize()
        self.assertEqual(client.session_id, "sess-1")
        self.assertEqual(client.server_info, {"name": "hubitat-mcp-gateway", "version": "0.1.0"})
        self.assertEqual(info["protocolVersion"], "2025-06-18")
        self.assertEqual(len(t.messages("notifications/initialized")), 1)

    def test_session_and_auth_headers_on_followups(self):
        t = FakeTransport(healthy)
        client = hub_mcp.MCPClient("http://h/mcp", "sekret", t)
        client.initialize()
        client.list_tools()
        followup = [c for c in t.calls if (c["message"] or {}).get("method") == "tools/list"][0]
        self.assertEqual(followup["headers"]["Mcp-Session-Id"], "sess-1")
        self.assertEqual(followup["headers"]["Authorization"], "Bearer sekret")

    def test_list_tools_follows_pagination(self):
        t = FakeTransport(healthy)
        client = hub_mcp.MCPClient("http://h/mcp", "tok", t)
        client.initialize()
        names = [x["name"] for x in client.list_tools()]
        self.assertEqual(names, ["list_modes", "get_device"])

    def test_call_tool_unwraps_double_encoded_text(self):
        t = FakeTransport(healthy)
        client = hub_mcp.MCPClient("http://h/mcp", "tok", t)
        client.initialize()
        out = client.call_tool("list_modes", {})
        self.assertEqual(out["data"], {"currentMode": {"id": 2, "name": "Evening"}})
        self.assertFalse(out["isError"])

    def test_401_raises_actionable_error(self):
        def unauth(message, headers):
            return (401, {"WWW-Authenticate": "Bearer"}, '{"error":"unauthorized"}')
        client = hub_mcp.MCPClient("http://h/mcp", "tok", FakeTransport(unauth))
        with self.assertRaises(HubError) as ctx:
            client.initialize()
        self.assertIn("401", str(ctx.exception))
        self.assertIn("token", str(ctx.exception).lower())


# ------------------------- main() / CLI -------------------------

class TestMain(unittest.TestCase):
    def _run(self, argv, transport):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = hub_mcp.main(argv, transport=transport)
        return rc, out.getvalue(), err.getvalue()

    def test_list_tools_ok(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run(
                ["list-tools", "--url", "http://h/mcp", "--token-file", token_file(d)],
                FakeTransport(healthy))
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertEqual(payload["tool_count"], 2)
            self.assertEqual(payload["server"], {"name": "hubitat-mcp-gateway", "version": "0.1.0"})

    def test_initialize_prints_instructions(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run(
                ["initialize", "--ip", "192.0.2.15", "--token-file", token_file(d)],
                FakeTransport(healthy))
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["instructions"], "Prefer read-only tools.")

    def test_call_ok_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run(
                ["call", "list_modes", "--url", "http://h/mcp", "--token-file", token_file(d)],
                FakeTransport(healthy))
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["result"], {"currentMode": {"id": 2, "name": "Evening"}})

    def test_call_tool_error_returns_one(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run(
                ["call", "boom", "--url", "http://h/mcp", "--token-file", token_file(d)],
                FakeTransport(healthy))
            self.assertEqual(rc, 1)
            self.assertTrue(json.loads(out)["isError"])

    def test_allow_sensitive_merges_flag_into_arguments(self):
        t = FakeTransport(healthy)
        with tempfile.TemporaryDirectory() as d:
            rc, out, _ = self._run(
                ["call", "hubitat_lock", "--args", '{"device":"Front Door"}',
                 "--allow-sensitive", "--url", "http://h/mcp", "--token-file", token_file(d)], t)
            self.assertEqual(rc, 0)
            sent = t.messages("tools/call")[0]["params"]["arguments"]
            self.assertEqual(sent, {"device": "Front Door", "allowSensitive": True})

    def test_bad_args_json_is_usage_error(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = self._run(
                ["call", "list_modes", "--args", "{not json", "--url", "http://h/mcp",
                 "--token-file", token_file(d)], FakeTransport(healthy))
            self.assertEqual(rc, 2)
            self.assertIn("not valid JSON", err)

    def test_missing_token_is_config_error(self):
        saved = os.environ.pop("HUBITAT_MCP_TOKEN", None)
        try:
            rc, _, err = self._run(["list-tools", "--url", "http://h/mcp"], FakeTransport(healthy))
            self.assertEqual(rc, 2)
            self.assertIn("HUBITAT_MCP_TOKEN", err)
        finally:
            if saved is not None:
                os.environ["HUBITAT_MCP_TOKEN"] = saved

    def test_no_target_is_config_error(self):
        with tempfile.TemporaryDirectory() as d:
            rc, _, err = self._run(["list-tools", "--token-file", token_file(d)], FakeTransport(healthy))
            self.assertEqual(rc, 2)
            self.assertIn("--url", err)


if __name__ == "__main__":
    unittest.main()
