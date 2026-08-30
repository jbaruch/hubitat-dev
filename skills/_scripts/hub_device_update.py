#!/usr/bin/env python3
"""Rebuild and post a Hubitat device's `/device/update` field set with the correct boolean
encoding, and report what actually moved.

Grounded live on 2.5.1.135 and 2.5.1.169 (C-8 Pro, Hub Security off — see
../_reference/endpoints.md):

    GET  /device/fullJson/<id>  -> {device:{id, name, label, ... version}, ...}
    POST /device/update         form-urlencoded, the FULL field set, omissions clear values

Two properties of that endpoint make a hand-rolled POST dangerous, and both are encoded here
rather than left to the caller:

1. **Every boolean is checkbox-semantic.** `meshEnabled`, `meshFullSync`, `retryEnabled` and
   `homeKitEnabled` are sent as `on` when true and OMITTED when false. Posting the literal string
   `"true"` CLEARS the field. Mis-encoding the mesh pair unshares the device, removing the mirror
   on the consuming hub and breaking every app bound to it.
2. **`version` is an optimistic-concurrency stamp**, so it is read fresh immediately before each
   POST and never cached across calls.

`--noop` rebuilds the current state and posts it unchanged: a round-trip that should move nothing.
It is the cheap proof that the encoding is right for a field set you have not posted before, and
the recommended first call against any unfamiliar device. What moved is reported in three buckets —
`applied` for a `--set` field that reached the value asked for, `benign_normalization` for the
empty-value normalizations the hub performs on its own, and `unexpected_drift` for everything else,
including a requested change the hub silently did not honour. A non-empty `unexpected_drift` is the
only failure: on a `--noop` it means the round-trip is lossy, and on a `--set` it means the write
did not land as asked. Either way, do not follow it with a further edit.

The deterministic pieces (form construction, boolean encoding, drift classification) are pure
functions unit-tested without a hub; only the fetch/post functions touch the network via an
injectable `transport`.

Usage:
    hub_device_update.py --ip 192.0.2.11 --device 953 --noop
    hub_device_update.py --hub main --device 1694 --noop
    hub_device_update.py --ip 192.0.2.11 --device 953 --set label="Kitchen Leak"
    hub_device_update.py --ip 192.0.2.11 --device 953 --set homeKitEnabled=true
    hub_device_update.py --ip 192.0.2.11 --device 953 --dry-run --set label=X   # print the form, post nothing
--noop and --set are mutually exclusive, and --noop and --dry-run are contradictory; --set may
repeat and may be combined with --dry-run. --set cannot reach `id` or `version`: the script owns
both. Output: one JSON
object on stdout ({hub, device_id, mode, form, posted, applied, benign_normalization,
unexpected_drift}) — the same keys in every mode, with empty buckets under --dry-run.
Exit 2 on a config or argument error, 1 on a hub/fetch error or unexpected drift, 0 otherwise.
"""
import argparse
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
# E402: this import must follow the sys.path insert above so hubclient resolves when run as a script.
from hubclient import HubError, _urllib_transport, resolve_base_from_args  # noqa: E402

DEVICE_PATH = "/device/fullJson/"
UPDATE_PATH = "/device/update"

# The full field set the device edit form posts, in the hub's own order. Omitting a key CLEARS it,
# so the form is always rebuilt whole from a fresh read (../_reference/endpoints.md).
FORM_FIELDS = (
    "name", "label", "zigbeeId", "maxEvents", "maxStates", "spammyThreshold",
    "deviceNetworkId", "deviceTypeId", "deviceTypeReadableType", "roomId",
    "meshEnabled", "retryEnabled", "meshFullSync", "homeKitEnabled",
    "locationId", "hubId", "groupId", "dashboardIds", "tags", "defaultIcon",
    "notes", "id", "version", "controllerType",
)

# Checkbox-semantic: emitted as `on` when true, omitted entirely when false. There is no second
# encoding on this form — a literal "true" clears the field. Measured 2.5.1.135, 2026-07-27.
CHECKBOX_FIELDS = frozenset({"meshEnabled", "meshFullSync", "retryEnabled", "homeKitEnabled"})

# Empty-value normalizations the hub applies on its own. A freshly linked hub-mesh mirror is born
# with label=null and roomId=null and reports these on its first round-trip; they are not drift.
BENIGN_NORMALIZATIONS = (
    ("roomId", None, 0),
    ("label", None, ""),
)

# The script owns these two: `id` selects the device (from --device, and what verification re-reads)
# and `version` is the concurrency stamp read fresh immediately before the POST. Letting --set reach
# either one silently retargets the write or defeats the stamp, so both are argument errors.
SCRIPT_OWNED_FIELDS = frozenset({"id", "version"})

_TRUE_STRINGS = frozenset({"true", "on", "yes", "1"})
_FALSE_STRINGS = frozenset({"false", "off", "no", "0", ""})


def device_of(full: dict) -> dict:
    """Pure. The device record from a fullJson payload."""
    device = full.get("device")
    return device if isinstance(device, dict) else {}


def as_bool(value) -> bool:
    """Pure. Coerce a fullJson boolean-ish value to a bool.

    The hub renders these as real booleans, as the strings "true"/"false", and as null (absent).
    A freshly linked mesh mirror reads null for fields it has never had set.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE_STRINGS:
        return True
    if text in _FALSE_STRINGS:
        return False
    raise HubError(f"cannot read {value!r} as a boolean — expected true/false/on/off/null")


def scalar(value) -> str:
    """Pure. Render a non-boolean field for the form. None becomes the empty string, which is what
    the browser form submits for an untouched empty input."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(scalar(v) for v in value)
    return str(value)


def build_form(full: dict, changes: Optional[dict] = None) -> list:
    """Pure. The full `/device/update` form as an ordered list of (key, value) pairs.

    Booleans in CHECKBOX_FIELDS are emitted as `on` when true and omitted when false — the encoding
    this whole script exists to get right. Every other field is sent as a string, whole, because
    an omitted key clears the value on this endpoint.
    """
    device = device_of(full)
    if not device:
        raise HubError("fullJson carried no device record — cannot rebuild the update form")
    pending = dict(changes or {})
    unknown = sorted(set(pending) - set(FORM_FIELDS))
    if unknown:
        raise HubError(f"not fields on this form: {', '.join(unknown)} — "
                       f"valid: {', '.join(FORM_FIELDS)}")

    pairs = []
    for field in FORM_FIELDS:
        value = pending[field] if field in pending else device.get(field)
        if field in CHECKBOX_FIELDS:
            if as_bool(value):
                pairs.append((field, "on"))
            # false -> omitted entirely. Emitting "false" here is the destructive bug.
            continue
        pairs.append((field, scalar(value)))
    return pairs


def encode_form(pairs: list) -> str:
    """Pure. urlencode the ordered pairs exactly as the browser form does."""
    return urllib.parse.urlencode(pairs)


def read_fields(full: dict) -> dict:
    """Pure. The comparable projection of a device record: every form field, booleans normalized."""
    device = device_of(full)
    out = {}
    for field in FORM_FIELDS:
        value = device.get(field)
        out[field] = as_bool(value) if field in CHECKBOX_FIELDS else value
    return out


def version_advanced(before: dict, after: dict) -> bool:
    """Pure. Whether the concurrency stamp moved forward across the POST.

    The hub bumps `version` on every save (../_reference/endpoints.md), so a stamp that did not
    advance means the write was not applied — a stale-version rejection is success-shaped on this
    endpoint, answering 200 while changing nothing. Without this check a rejected `--noop` reads as
    a clean round-trip, which is the opposite of what the caller asked.
    """
    was, now = before.get("version"), after.get("version")
    if was is None or now is None:
        return False
    try:
        return int(now) > int(was)
    except (TypeError, ValueError):
        return False


def same_value(field: str, left, right) -> bool:
    """Pure. Whether two readings of a field mean the same thing.

    A requested value arrives as the string the caller typed while the hub reads it back in its own
    type (`roomId=8` vs `8`), so scalars compare as the form renders them.
    """
    if field in CHECKBOX_FIELDS:
        return as_bool(left) == as_bool(right)
    return scalar(left) == scalar(right)


def classify_drift(before: dict, after: dict, changes: Optional[dict] = None,
                   ignore=("version",)) -> tuple:
    """Pure. Split the before/after difference into (applied, benign_normalization,
    unexpected_drift).

    A field the caller asked to change is compared against the REQUESTED value, not the prior one:
    landing the change is success, so it belongs in `applied`, and only a requested change the hub
    did not honour falls through to `unexpected_drift` (carrying `requested` so the caller can see
    what was asked). Every other field still compares before/after, so collateral drift on an
    untouched field is caught exactly as it is on a `--noop`.

    `version` is excluded by default: a successful POST bumps it, so it always differs and its
    change is the proof the write landed rather than evidence of drift.
    """
    changes = changes or {}
    applied, benign, unexpected = {}, {}, {}
    for field in FORM_FIELDS:
        if field in ignore:
            continue
        was, now = before.get(field), after.get(field)
        if field in changes:
            if same_value(field, changes[field], now):
                applied[field] = {"before": was, "after": now}
            else:
                unexpected[field] = {"before": was, "after": now, "requested": changes[field]}
            continue
        if was == now:
            continue
        move = {"before": was, "after": now}
        if any(field == f and was == b and now == a for f, b, a in BENIGN_NORMALIZATIONS):
            benign[field] = move
        else:
            unexpected[field] = move
    return applied, benign, unexpected


def parse_set(assignments: list) -> dict:
    """Pure. Turn repeated `--set key=value` into a change dict, coercing checkbox fields to bool."""
    changes = {}
    for item in assignments or []:
        if "=" not in item:
            raise HubError(f"--set expects key=value, got {item!r}")
        key, _, value = item.partition("=")
        key = key.strip()
        if key in SCRIPT_OWNED_FIELDS:
            raise HubError(f"--set {key} is not allowed: the script owns it — the device comes from "
                           f"--device and `version` is read fresh immediately before the POST")
        changes[key] = as_bool(value) if key in CHECKBOX_FIELDS else value
    return changes


def fetch_full_json(base: str, device_id, transport) -> dict:
    """Read the device record fresh. `version` must come from here immediately before each POST."""
    status, _, body = transport("GET", f"{base}{DEVICE_PATH}{device_id}", None)
    if status != 200:
        raise HubError(f"GET {DEVICE_PATH}{device_id} returned HTTP {status} — "
                       f"check the device id and that Hub Security is off for this client")
    try:
        return json.loads(body)
    except ValueError as e:
        raise HubError(f"GET {DEVICE_PATH}{device_id} did not return JSON: {e}") from e


def post_update(base: str, pairs: list, transport) -> int:
    """Post the rebuilt form. The hub answers HTML or a redirect, never JSON."""
    status, _, _ = transport("POST", f"{base}{UPDATE_PATH}", encode_form(pairs),
                             "application/x-www-form-urlencoded")
    if status not in (200, 302):
        raise HubError(f"POST {UPDATE_PATH} returned HTTP {status} — the field set was not applied; "
                       f"re-read the device before retrying, the version stamp may be stale")
    return status


def main(argv=None, transport=None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild and post a Hubitat device's /device/update field set, correctly encoded.")
    parser.add_argument("--ip")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--hub")
    parser.add_argument("--hubs-file")
    parser.add_argument("--device", type=int, required=True)
    parser.add_argument("--noop", action="store_true",
                        help="repost current state unchanged and report drift")
    parser.add_argument("--set", dest="assignments", action="append", metavar="KEY=VALUE",
                        help="change a field; repeatable")
    parser.add_argument("--dry-run", action="store_true",
                        help="build and print the form, post nothing")
    args = parser.parse_args(argv)
    transport = transport or _urllib_transport

    if not args.noop and not args.assignments and not args.dry_run:
        print("nothing to do — pass --noop to round-trip current state, or --set KEY=VALUE to edit",
              file=sys.stderr)
        return 2
    if args.noop and args.assignments:
        print("--noop reposts current state unchanged; it cannot be combined with --set",
              file=sys.stderr)
        return 2
    if args.noop and args.dry_run:
        print("--noop posts current state and --dry-run posts nothing; pass one or the other",
              file=sys.stderr)
        return 2

    try:
        base = resolve_base_from_args(args.ip, args.port, args.hub, args.hubs_file)
        changes = parse_set(args.assignments)
    except (HubError, OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2

    try:
        full = fetch_full_json(base, args.device, transport)
        before = read_fields(full)
        pairs = build_form(full, changes)

        if args.dry_run:
            print(json.dumps({"hub": base, "device_id": args.device, "mode": "dry-run",
                              "form": pairs, "posted": False, "applied": {},
                              "benign_normalization": {}, "unexpected_drift": {}},
                             indent=2, default=str))
            return 0

        post_update(base, pairs, transport)
        after = read_fields(fetch_full_json(base, args.device, transport))
        if not version_advanced(before, after):
            raise HubError(
                f"the version stamp did not advance ({before.get('version')} -> "
                f"{after.get('version')}) — the hub answered but did not apply the write, which is "
                f"what a stale-stamp rejection looks like on this endpoint. Re-read the device and "
                f"retry; if the stamp never advances on this platform build, the postcondition "
                f"here is wrong and not the write")
        applied, benign, unexpected = classify_drift(before, after, changes)
    except HubError as e:
        print(str(e), file=sys.stderr)
        return 1

    result = {
        "hub": base,
        "device_id": args.device,
        "mode": "noop" if args.noop else "update",
        "form": pairs,
        "posted": True,
        "applied": applied,
        "benign_normalization": benign,
        "unexpected_drift": unexpected,
    }
    print(json.dumps(result, indent=2, default=str))
    if unexpected:
        print(f"unexpected drift on {len(unexpected)} field(s): {', '.join(sorted(unexpected))} — "
              f"a requested change the hub did not honour, or a field that moved on its own; "
              f"re-read the device and do not post a further edit until it is explained",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
