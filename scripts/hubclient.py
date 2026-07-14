#!/usr/bin/env python3
"""Shared Hubitat hub client: config resolution + code enumerate/pull/deploy over the
undocumented editor endpoints (see reference/endpoints.md, grounded on 2.5.1.125).

Not a CLI — imported by hub_pull.py and hub_deploy.py. The HTTP layer is a single
injectable `transport` callable so the deterministic logic (URL building, deploy
create-vs-update decision, version handling, response parsing) is unit-testable
without a live hub. Local network, Hub Security off — no auth. If a hub enables
Hub Security every call needs a session cookie first; this client does not do that.

Config (hubs.json, owned by the hub-config skill) holds only IPs — never secrets:
    {"schema_version": 1, "default": "main",
     "hubs": {"main": {"ip": "192.168.30.2", "port": 8080}, ...}}
Maker API credentials, if used elsewhere, come from the environment, not this file.
"""
import json
import urllib.request
import urllib.error
from typing import Optional
from urllib.parse import urlencode

SCHEMA_VERSION = 1

# kind -> endpoint paths. `enumerate` lists code entries; `code` reads source+version;
# `create` makes a new entry; `update` saves an existing one (needs current version).
_PATHS = {
    "app": {"enumerate": "/hub2/userAppTypes", "code": "/app/ajax/code",
            "create": "/app/save", "update": "/app/ajax/update", "editor": "/app/editor/"},
    "driver": {"enumerate": "/hub2/userDeviceTypes", "code": "/driver/ajax/code",
               "create": "/driver/save", "update": "/driver/ajax/update", "editor": "/driver/editor/"},
    "library": {"enumerate": "/hub2/userLibraries", "code": "/library/ajax/code",
                "create": "/library/save", "update": "/library/ajax/update", "editor": "/library/editor/"},
}

KINDS = tuple(_PATHS)


class DeployConflict(Exception):
    """Raised when the hub rejects an update because the version is stale — a newer edit
    exists on the hub. Re-pull and reconcile; never blindly retry with a bumped number."""


class HubError(Exception):
    """A hub call failed or returned an unusable response."""


def base_url(ip: str, port: int = 8080) -> str:
    return f"http://{ip}:{port}"


def resolve_hub(hubs_config: dict, name: Optional[str] = None) -> dict:
    """Return {name, ip, port, base} for the named hub, or the config default.
    Pure — takes the already-loaded config dict."""
    hubs = hubs_config.get("hubs") or {}
    if not hubs:
        raise HubError("hubs.json has no 'hubs' entries")
    chosen = name or hubs_config.get("default")
    if not chosen:
        if len(hubs) == 1:
            chosen = next(iter(hubs))
        else:
            raise HubError("no hub name given and no 'default' set in hubs.json")
    if chosen not in hubs:
        raise HubError(f"hub '{chosen}' not in hubs.json (have: {', '.join(sorted(hubs))})")
    entry = hubs[chosen]
    port = int(entry.get("port", 8080))
    return {"name": chosen, "ip": entry["ip"], "port": port, "base": base_url(entry["ip"], port)}


def resolve_base_from_args(ip: Optional[str] = None, port: int = 8080,
                           hub: Optional[str] = None, hubs_path: Optional[str] = None) -> str:
    """Resolve a hub base URL from CLI-style inputs: an explicit --ip wins; otherwise read
    hubs.json and pick the named hub (or its default)."""
    if ip:
        return base_url(ip, port)
    if hubs_path is None and hub:
        hubs_path = "hubs.json"  # a named hub with no explicit path resolves against ./hubs.json
    if hubs_path:
        return resolve_hub(load_hubs(hubs_path), hub)["base"]
    raise HubError("provide --ip <addr>, --hub <name> with a hubs.json in the working directory, "
                   "or --hubs <path>")


def load_hubs(path) -> dict:
    try:
        with open(path) as f:
            cfg = json.load(f)
    except FileNotFoundError as e:
        raise HubError(
            f"hub config {path} not found — create it with hubs_config.py "
            f"(init/add), or pass --ip <addr> instead of --hub.") from e
    except (OSError, json.JSONDecodeError) as e:
        raise HubError(f"hub config {path} could not be read as JSON: {e}") from e
    ver = cfg.get("schema_version")
    if ver != SCHEMA_VERSION:
        raise HubError(f"hub config {path} schema_version {ver} != {SCHEMA_VERSION} (this client's version)")
    return cfg


def _urllib_transport(method: str, url: str, body: Optional[str]):
    """Default transport. Returns (status, headers_dict, text). The create POST 302-redirects
    to the new entry's editor URL (/app|driver/editor/<id>); urllib follows it, so the new id
    is read from the final URL, surfaced here as a synthetic 'Location' header."""
    data = body.encode() if body is not None else None
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        h = dict(resp.headers)
        h.setdefault("Location", resp.geturl())  # final URL after any redirect
        return resp.status, h, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), (e.read().decode("utf-8", "replace") if e.fp else "")
    except urllib.error.URLError as e:
        raise HubError(f"cannot reach {url}: {e.reason}") from e


def _id_from_location(headers: dict, kind: str):
    loc = headers.get("Location") or headers.get("location") or ""
    marker = _PATHS[kind]["editor"]
    if marker in loc:
        tail = loc.split(marker, 1)[1].strip("/").split("/")[0].split("?")[0]
        if tail.isdigit():
            return int(tail)
    return None


def decide_deploy_action(existing_id, existing_version):
    """Pure. Given the resolved existing entry (or None), decide create vs update."""
    if existing_id is None:
        return {"action": "create"}
    return {"action": "update", "id": existing_id, "version": existing_version}


class HubClient:
    def __init__(self, base: str, transport=None):
        self.base = base.rstrip("/")
        self._t = transport or _urllib_transport

    def _get(self, path: str):
        return self._t("GET", self.base + path, None)

    def _post_form(self, path: str, fields: dict):
        return self._t("POST", self.base + path, urlencode(fields))

    def _json(self, text: str, path: str):
        """Parse a hub response as JSON, or raise an actionable HubError. A hub with Hub
        Security on (or a changed endpoint) returns an HTML login/error page, not JSON."""
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise HubError(
                f"{self.base}{path} did not return JSON (got {text[:80]!r}). Check that Hub "
                f"Security is off on this hub and that the endpoint is valid on its firmware.") from e

    def enumerate(self, kind: str) -> list:
        path = _PATHS[kind]["enumerate"]
        status, _, text = self._get(path)
        if status != 200:
            raise HubError(f"enumerate {kind} returned HTTP {status} from {self.base}{path}")
        return self._json(text, path)

    def pull(self, kind: str, code_id: int) -> dict:
        path = f"{_PATHS[kind]['code']}?id={code_id}"
        status, _, text = self._get(path)
        if status != 200:
            raise HubError(f"pull {kind} id={code_id} returned HTTP {status} from {self.base}{path}")
        data = self._json(text, path)
        return {"id": data.get("id", code_id), "name": data.get("name"),
                "version": data.get("version"), "source": data.get("source", "")}

    def find_id(self, kind: str, name: str):
        """Match an existing code entry by its declared name. Returns id or None."""
        for entry in self.enumerate(kind):
            if entry.get("name") == name:
                return entry.get("id")
        return None

    def deploy(self, kind: str, source: str, code_id=None) -> dict:
        """Create a new entry or update an existing one. When code_id is None the current
        version is fetched first (optimistic concurrency). Returns {action, id, version?}."""
        version = None
        if code_id is not None:
            version = self.pull(kind, code_id)["version"]
        plan = decide_deploy_action(code_id, version)

        if plan["action"] == "create":
            status, headers, _ = self._post_form(
                _PATHS[kind]["create"], {"id": "", "version": "", "create": "", "source": source})
            if status not in (200, 302):
                raise HubError(f"create {kind} returned HTTP {status}")
            new_id = _id_from_location(headers, kind)
            if new_id is None:
                raise HubError(
                    f"create {kind} returned HTTP {status} but no new id was found in the "
                    f"redirect (Location: {headers.get('Location')!r}). The create may have "
                    f"failed — re-enumerate the hub before deploying again.")
            return {"action": "create", "id": new_id}

        status, _, text = self._post_form(
            _PATHS[kind]["update"], {"id": plan["id"], "version": plan["version"], "source": source})
        # Confirm via the parsed JSON status field, exactly "success" — a substring match
        # would wrongly accept {"status":"unsuccessful"} or an HTML page mentioning "success".
        # The hub's /ajax/update returns {"status":"success"}.
        try:
            confirmed = json.loads(text).get("status") == "success"
        except (json.JSONDecodeError, AttributeError):
            confirmed = False
        if confirmed:
            return {"action": "update", "id": plan["id"], "version": plan["version"]}
        if "version" in text.lower():
            raise DeployConflict(
                f"hub rejected the update for {kind} id={plan['id']} — the hub has a newer "
                f"version than {plan['version']}. Re-pull and reconcile before deploying.")
        raise HubError(
            f"update {kind} id={plan['id']} did not confirm success (HTTP {status}): {text[:200]}")
