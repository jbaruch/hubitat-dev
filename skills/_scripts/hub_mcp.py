#!/usr/bin/env python3
"""Client for the first-party Hubitat **AI (MCP) Connector Integration** — list its tools
and call one, over the hub's local MCP endpoint.

Grounded live 2026-08-10 on 2.5.1.140 (C-8 Pro, local network) at http://<hub-ip>/mcp
(see ../_reference/mcp-connector.md):

  - Built-in integration app (Integrations -> Add Built-In Integration -> enable). **Local only**
    (cloud agents unsupported at this time). Endpoint is port **80**, path /mcp — NOT the 8080 admin UI.
  - Transport: **Streamable HTTP** — JSON-RPC 2.0 over POST /mcp; an SSE stream is offered on
    GET /mcp for resource notifications (not used here). Protocol 2025-06-18, server
    hubitat-mcp-gateway 0.1.0. A session id comes back in the `Mcp-Session-Id` response header on
    initialize and is echoed on every following request.
  - Auth: `Authorization: Bearer <token>`. Unauthenticated -> 401 {"error":"unauthorized"} with
    `WWW-Authenticate: Bearer`. **The token is a secret** — this client reads it from
    $HUBITAT_MCP_TOKEN or a --token-file and NEVER prints it, echoes it, or names its value in an
    error; only the *source* is named. Never pass a token as a CLI literal (it lands in process
    listings and shell history). Reset it in the app if compromised. The endpoint URL is validated
    local-only before any token is loaded or sent: an IP literal must be private/loopback/link-local,
    a hostname is resolved with every address required local, and the request is pinned to the
    validated IP — the token never leaves for an external, mistyped, or DNS-rebinding host.
  - A tools/call `result.content[].text` is itself a **JSON document string** (double-encoded); this
    client unwraps it so the caller gets structured data, not a string to re-parse.
  - Some tools require `allowSensitive: true` in the arguments; --allow-sensitive merges it. Which
    tools require it is owned by the gateway and drifts with the app — read each tool's live
    description/schema (`list-tools --schemas`), never a hard-coded list.

The deterministic pieces (request building, JSON/SSE body parsing, JSON-RPC error handling, the
content-unwrap, session-id extraction, token/URL resolution) are pure functions unit-tested with an
injectable `transport`; only the network call touches urllib.

Usage:
    hub_mcp.py list-tools --ip 192.168.1.15
    hub_mcp.py list-tools --url http://192.168.1.15/mcp --schemas
    hub_mcp.py initialize --hub main                      # handshake only: serverInfo + instructions
    hub_mcp.py call hubitat_search_devices --args '{"query":"patio"}' --ip 192.168.1.15
    hub_mcp.py call hubitat_lock --args '{"device":"Front Door"}' --allow-sensitive --ip 192.168.1.15
    hub_mcp.py call list_modes --hub main                 # ip from ./hubs.json, forced to port 80 /mcp
Token: `export HUBITAT_MCP_TOKEN=...` (preferred) or `--token-file ~/.hubitat/mcp_token`.
Output: one JSON object on stdout. Exit 2 on a config/usage error, 1 on a hub/tool error, 0 otherwise.
"""
import argparse
import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
# E402: this import must follow the sys.path insert above so hubclient resolves when run as a script.
from hubclient import HubError, load_hubs, resolve_hub  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "hubitat-dev/hub_mcp", "version": "1"}
DEFAULT_PATH = "/mcp"


def build_request(method: str, params=None, req_id: Optional[int] = 1) -> dict:
    """Pure. A JSON-RPC 2.0 request (or notification when req_id is None)."""
    msg: dict = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        msg["id"] = req_id
    if params is not None:
        msg["params"] = params
    return msg


def parse_message_body(text: str) -> dict:
    """Pure. Parse a Streamable-HTTP response body into one JSON-RPC message. The gateway answers
    either as plain application/json or as an SSE stream (text/event-stream) whose `data:` lines
    carry the JSON — both are handled so the caller never has to know which was chosen."""
    text = (text or "").strip()
    if not text:
        raise HubError("empty response body from the MCP endpoint")
    if text[0] in "{[":
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise HubError("MCP endpoint returned a non-JSON body (withheld — it may reflect submitted "
                           "arguments); check the token, host, and that /mcp is the right endpoint.") from e
    payloads = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            frag = line[len("data:"):].strip()
            if frag and frag != "[DONE]":
                payloads.append(frag)
    if not payloads:
        raise HubError("MCP SSE response carried no data frames.")
    try:
        return json.loads(payloads[-1])  # one message per response here; the last data frame is it
    except json.JSONDecodeError as e:
        raise HubError("an MCP SSE data frame was not JSON (withheld — it may reflect submitted "
                       "arguments).") from e


def jsonrpc_result(msg):
    """Pure. Return the JSON-RPC `result`, or raise HubError on an `error` object or a malformed one.
    `msg` is whatever `json.loads` produced — a top-level array or scalar is caught by the guard."""
    if not isinstance(msg, dict):
        raise HubError("MCP response was not a JSON object.")
    if "error" in msg:
        err = msg.get("error") or {}
        code = err.get("code") if isinstance(err, dict) else None
        # The error `message` is withheld: a gateway validation error can reflect submitted
        # arguments (a lock PIN, an access code), and no-secrets forbids echoing it.
        raise HubError(f"MCP returned JSON-RPC error code {code} (message withheld — it may reflect "
                       f"submitted arguments).")
    if "result" not in msg:
        raise HubError("MCP response had neither result nor error.")
    return msg["result"]


def _maybe_json(text):
    """Pure. Parse a string as JSON when it looks like JSON, else return it unchanged. The gateway
    double-encodes: a content text part is itself a JSON document string."""
    if not isinstance(text, str):
        return text
    s = text.strip()
    if s[:1] in "{[":
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return text
    return text


def unwrap_tool_result(result) -> dict:
    """Pure. Flatten a tools/call result into {isError, data, text}. `content[]` text parts are
    collected; a single JSON text part is parsed (undoing the double-encoding), multiple parts become
    a list, and a result with no text falls back to `structuredContent`."""
    if not isinstance(result, dict):
        return {"isError": False, "data": result, "text": None}
    parts = result.get("content") or []
    texts = [str(p["text"]) for p in parts
             if isinstance(p, dict) and p.get("type") == "text" and p.get("text") is not None]
    is_error = bool(result.get("isError"))
    if len(texts) == 1:
        data = _maybe_json(texts[0])
    elif texts:
        data = [_maybe_json(t) for t in texts]
    else:
        data = result.get("structuredContent")
    return {"isError": is_error, "data": data, "text": ("\n".join(texts) if texts else None)}


def session_id_from_headers(headers: dict) -> Optional[str]:
    """Pure. The Mcp-Session-Id response header, case-insensitively. None when absent."""
    for k, v in (headers or {}).items():
        if str(k).lower() == "mcp-session-id":
            return v
    return None


def resolve_token(env=None, token_file: Optional[str] = None) -> str:
    """Resolve the bearer token from the environment or a file. The value is NEVER returned in any
    error text or printed anywhere — only the *source* is named. Env var wins over the file."""
    env = env if env is not None else os.environ
    tok = env.get("HUBITAT_MCP_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    if token_file:
        try:
            content = Path(token_file).read_text()
        except OSError as e:
            raise HubError(f"cannot read token file {token_file}: {e}") from e
        if not content.strip():
            raise HubError(f"token file {token_file} is empty")
        return content.strip()
    raise HubError(
        "no MCP bearer token — set HUBITAT_MCP_TOKEN or pass --token-file <path>. The token is shown "
        "in the hub's AI (MCP) Connector app; treat it as a secret (never a CLI literal), and reset "
        "it in that app if it may be compromised.")


# Explicit LAN ranges the bearer token may be sent to. `ipaddress.is_private` is too broad — it also
# returns True for unspecified (0.0.0.0, ::) and reserved/documentation ranges (192.0.2.0/24,
# 2001:db8::/32, 100.64.0.0/10, …), none of which are local hub addresses (coding-policy `rules/no-secrets.md`).
_LOCAL_NETS = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",  # RFC1918
    "169.254.0.0/16", "127.0.0.0/8",                   # IPv4 link-local, loopback
    "fc00::/7", "fe80::/10", "::1/128",                # IPv6 ULA, link-local, loopback
))


def _addr_is_local(ipstr: str) -> bool:
    """Pure. True only when an IP string falls in an explicit LAN range — RFC1918/ULA, loopback, or
    link-local. Unspecified and reserved/documentation ranges (which `is_private` also accepts) are
    rejected so the token is never sent to an unintended route."""
    try:
        addr = ipaddress.ip_address(ipstr.strip("[]"))
    except ValueError:
        return False
    return any(addr in net for net in _LOCAL_NETS)


def _resolve_host(host: str) -> list:
    """Resolve a hostname to its distinct IP strings via getaddrinfo. Indirected through the
    module-level `resolve_host` seam so tests substitute a resolver and never touch real DNS."""
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


resolve_host = _resolve_host  # test seam; validate_local_mcp_url calls this name


def validate_local_mcp_url(url: str) -> str:
    """Reject any URL the bearer token must not be sent to, and pin the connection to a validated
    local address. The AI (MCP) Connector is local-only by contract and the token is password-grade,
    so a mistyped/external host — or a single-label name that DNS search config expands to a public
    address — would disclose the secret (coding-policy `rules/no-secrets.md`). An IP literal is checked directly; a
    hostname is RESOLVED and EVERY resolved address must be private/loopback/link-local, after which
    the URL is rewritten to the validated IP so the request connects to the address just checked
    (defeating a DNS-rebinding swap between check and connect). Enforces http(s) and the /mcp path
    too, before any token is loaded or sent. Raises HubError otherwise."""
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        # Never echo the credentials — reject before any output prints the URL (coding-policy `rules/no-secrets.md`).
        raise HubError("MCP URL must not embed credentials (user:pass@…) — the bearer token is the "
                       "only auth; pass a plain http://<hub-ip>/mcp.")
    if parsed.scheme not in ("http", "https"):
        raise HubError(f"MCP URL must be http or https, got {url!r}")
    try:
        port = parsed.port  # urlparse defers port parsing; a malformed port raises ValueError here
    except ValueError as e:
        raise HubError(f"MCP URL has an invalid port in {url!r}: {e}. Pass a valid --url or --ip.") from e
    host = parsed.hostname or ""
    if not host:
        raise HubError(f"MCP URL has no host: {url!r}")
    if parsed.path.rstrip("/") != "/mcp":
        raise HubError(f"unexpected MCP path {parsed.path!r} on {host!r} — the connector endpoint is /mcp.")

    # An IP literal is validated directly — no resolution.
    try:
        ipaddress.ip_address(host.strip("[]"))
        if not _addr_is_local(host):
            raise HubError(
                f"refusing to send the bearer token to non-local host {host!r} — the AI (MCP) "
                f"Connector is local-only. Use the hub's private/loopback address.")
        return url
    except ValueError:
        pass  # not an IP literal — resolve the hostname below

    # A hostname (localhost, an mDNS *.local name, or a bare single-label name) is resolved; every
    # resolved address must be local, then the URL is pinned to a validated address.
    try:
        addrs = resolve_host(host)
    except OSError as e:
        raise HubError(
            f"cannot resolve MCP host {host!r}: {e}. Pass the hub's IP directly with --ip instead.") from e
    if not addrs:
        raise HubError(f"MCP host {host!r} resolved to no addresses — pass the hub's IP directly with --ip.")
    nonlocal_hits = [a for a in addrs if not _addr_is_local(a)]
    if nonlocal_hits:
        raise HubError(
            f"refusing to send the bearer token: host {host!r} resolves to non-local address(es) "
            f"{nonlocal_hits} — the AI (MCP) Connector is local-only.")
    pinned = addrs[0]
    netloc = f"[{pinned}]" if ":" in pinned else pinned
    if port:
        netloc = f"{netloc}:{port}"
    return parsed._replace(netloc=netloc).geturl()


def _ip_to_netloc(ip: str) -> str:
    """Bracket an IPv6 literal for use in a URL netloc (`::1` → `[::1]`); leave IPv4 and hostnames
    unchanged. Without the brackets an IPv6 address's colons are misparsed as a port."""
    try:
        if ipaddress.ip_address(ip).version == 6:
            return f"[{ip}]"
    except ValueError:
        pass
    return ip


def resolve_mcp_url(url: Optional[str] = None, ip: Optional[str] = None,
                    hub: Optional[str] = None, hubs_path: Optional[str] = None) -> str:
    """Resolve and validate the MCP endpoint URL. An explicit --url wins; --ip and --hub both force
    port 80 and the /mcp path (the connector is NOT on the 8080 admin port that hubs.json records).
    Every path runs through validate_local_mcp_url so the token never leaves for a non-local host."""
    if url:
        return validate_local_mcp_url(url)
    if ip:
        return validate_local_mcp_url(f"http://{_ip_to_netloc(ip)}{DEFAULT_PATH}")
    if hub:
        resolved = resolve_hub(load_hubs(hubs_path or "hubs.json"), hub)
        return validate_local_mcp_url(f"http://{_ip_to_netloc(resolved['ip'])}{DEFAULT_PATH}")
    raise HubError("provide --url http://<ip>/mcp, --ip <addr>, or --hub <name> (with a hubs.json)")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow HTTP redirects. urllib follows 3xx automatically and copies the request
    headers — including `Authorization: Bearer <token>` — to the redirect target, so a local MCP
    endpoint that 302s to a public URL would leak the bearer token past the pre-request local-address
    validation (coding-policy `rules/no-secrets.md`). Returning None here means "do not redirect"; the 3xx surfaces
    to the caller as a non-200 status instead, and the token never reaches the redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Opener with the default redirect handler REPLACED by the no-follow one, and proxies DISABLED (an
# empty ProxyHandler). A local-only endpoint must never route through a proxy: urllib otherwise honors
# http_proxy/https_proxy and would send `Authorization: Bearer <token>` to that (possibly external)
# proxy, leaking the secret past the pinned-local-IP check (coding-policy `rules/no-secrets.md`). Reused per request.
_OPENER = urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({}))


def _mcp_transport(method: str, url: str, body: Optional[str], headers: dict):
    """Default transport. Returns (status, headers_dict, text). Uses the no-redirect opener so an
    authenticated request never forwards the token to a redirect target. Raises HubError only on a
    transport failure (unreachable host); HTTP error statuses (including a blocked 3xx) are returned
    for the caller to classify."""
    data = body.encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        resp = _OPENER.open(req, timeout=20)
        return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # A blocked redirect arrives here as a 3xx HTTPError — a non-200 the caller rejects.
        return e.code, dict(e.headers or {}), (e.read().decode("utf-8", "replace") if e.fp else "")
    except urllib.error.URLError as e:
        raise HubError(f"cannot reach {url}: {e.reason}") from e


class MCPClient:
    """A minimal MCP client for the Hubitat gateway: initialize (with the mandated `initialized`
    notification), tools/list (following pagination), and tools/call (with the content unwrap).
    `transport` is injectable for testing; the token is held privately and only ever sent as a
    Bearer header, never surfaced."""

    def __init__(self, url: str, token: str, transport=None):
        self.url = url
        self._token = token
        self._t = transport or _mcp_transport
        self.session_id = None
        self.server_info = None
        self.protocol_version = None

    def _headers(self) -> dict:
        h = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _post(self, message: dict, expect_response: bool = True) -> dict:
        status, headers, text = self._t("POST", self.url, json.dumps(message), self._headers())
        if status == 401:
            raise HubError(
                "MCP endpoint returned 401 unauthorized — the bearer token was rejected. Check "
                "HUBITAT_MCP_TOKEN / --token-file against the token in the hub's AI (MCP) Connector "
                "app (reset it there and update your config if needed).")
        sid = session_id_from_headers(headers)
        if sid:
            self.session_id = sid
        if not expect_response:
            # A notification carries no JSON-RPC response body; the gateway answers 200/202 empty.
            if status not in (200, 202):
                raise HubError(f"MCP notification returned HTTP {status}.")
            return {}
        if status != 200:
            # The body is withheld: a server error can reflect submitted arguments (a lock PIN, an
            # access code), and no-secrets forbids echoing it (a 3xx blocked redirect also lands here).
            raise HubError(f"MCP endpoint returned HTTP {status} (body withheld — it may reflect "
                           f"submitted arguments).")
        result = jsonrpc_result(parse_message_body(text))
        if not isinstance(result, dict):
            raise HubError("MCP result was not a JSON object.")
        return result

    def initialize(self) -> dict:
        result = self._post(build_request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        }, req_id=1))
        self.server_info = result.get("serverInfo")
        self.protocol_version = result.get("protocolVersion")
        # The spec requires the client to announce it is initialized before issuing requests.
        self._post(build_request("notifications/initialized", None, req_id=None), expect_response=False)
        return result

    def list_tools(self) -> list:
        tools, cursor = [], None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._post(build_request("tools/list", params, req_id=2))
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict:
        result = self._post(build_request(
            "tools/call", {"name": name, "arguments": arguments or {}}, req_id=3))
        return unwrap_tool_result(result)


def _tool_summary(tool: dict, schemas: bool = False) -> dict:
    out = {"name": tool.get("name"), "description": (tool.get("description") or "")}
    if schemas:
        out["inputSchema"] = tool.get("inputSchema")
    return out


def _add_target_args(sp):
    sp.add_argument("--url", help="full MCP URL, e.g. http://192.168.1.15/mcp")
    sp.add_argument("--ip", help="hub IP -> http://<ip>/mcp (port 80)")
    sp.add_argument("--hub", help="named hub from hubs.json (uses its ip, forced to port 80 /mcp)")
    sp.add_argument("--hubs", help="path to hubs.json (default ./hubs.json when --hub is given)")
    sp.add_argument("--token-file", help="file holding the bearer token (else $HUBITAT_MCP_TOKEN)")


def main(argv=None, transport=None) -> int:
    p = argparse.ArgumentParser(description="Client for the Hubitat AI (MCP) Connector Integration.")
    sub = p.add_subparsers(dest="action", required=True)

    lt = sub.add_parser("list-tools", help="list the gateway's tools")
    _add_target_args(lt)
    lt.add_argument("--schemas", action="store_true", help="include each tool's inputSchema")

    ini = sub.add_parser("initialize", help="handshake only; print serverInfo, protocol, instructions")
    _add_target_args(ini)

    cl = sub.add_parser("call", help="call one tool and print its (unwrapped) result")
    _add_target_args(cl)
    cl.add_argument("tool", help="tool name (see list-tools)")
    cl.add_argument("--args", default="{}", help="tool arguments as a JSON object")
    cl.add_argument("--allow-sensitive", action="store_true",
                    help="merge allowSensitive=true, required by tools whose live description marks "
                         "them sensitive (see list-tools --schemas)")

    args = p.parse_args(argv)

    try:
        url = resolve_mcp_url(args.url, args.ip, args.hub, args.hubs)
        token = resolve_token(token_file=args.token_file)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 2

    arguments = None
    if args.action == "call":
        try:
            arguments = json.loads(args.args)
        except json.JSONDecodeError as e:
            print(f"--args is not valid JSON: {e}", file=sys.stderr)
            return 2
        if not isinstance(arguments, dict):
            print("--args must be a JSON object", file=sys.stderr)
            return 2
        if args.allow_sensitive:
            arguments["allowSensitive"] = True

    client = MCPClient(url, token, transport)
    try:
        info = client.initialize()
        if args.action == "initialize":
            print(json.dumps({
                "url": url,
                "serverInfo": info.get("serverInfo"),
                "protocolVersion": info.get("protocolVersion"),
                "capabilities": info.get("capabilities"),
                "instructions": info.get("instructions"),
            }, indent=2, default=str))
            return 0
        if args.action == "list-tools":
            tools = client.list_tools()
            print(json.dumps({
                "url": url,
                "server": info.get("serverInfo"),
                "tool_count": len(tools),
                "tools": [_tool_summary(t, args.schemas) for t in tools],
            }, indent=2, default=str))
            return 0
        # call — never echo `arguments`: an arbitrary tool's args can carry a lock PIN, access code,
        # or other secret the caller is setting, and stdout must not leak it (coding-policy `rules/no-secrets.md`).
        # The caller already knows what they passed via --args.
        outcome = client.call_tool(args.tool, arguments)
        # On a tool error the response can reflect submitted arguments (a lock PIN, an access code),
        # so withhold it (coding-policy `rules/no-secrets.md`); a successful result is the caller's requested data.
        result = ("<error result withheld — it may reflect submitted arguments>"
                  if outcome["isError"] else outcome["data"])
        print(json.dumps({
            "url": url,
            "tool": args.tool,
            "isError": outcome["isError"],
            "result": result,
        }, indent=2, default=str))
        # isError:true means the tool itself reported a failure (bad args, guard, device miss).
        return 1 if outcome["isError"] else 0
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
