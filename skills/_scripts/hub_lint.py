#!/usr/bin/env python3
"""Lint a Hubitat app or driver Groovy source for sandbox violations and silent-failure traps.

Offline and deterministic: given the same source it always emits the same findings.
It flags CANDIDATES for the `lint-review` skill to judge — the checks are heuristic
because Groovy is not fully regex-parseable (see coding-policy rules/script-delegation.md
"The Regex Trap"). Comments and string literals are stripped before scanning to cut
false positives, but a finding is a signal, not a verdict.

Checks:
  disallowed-import   an `import` whose class is not on the sandbox allow-list
  forbidden-construct sleep()/println/new Thread/.getClass() — blocked by the sandbox
  unresolved-handler  a subscribe/runIn/runOnce/runInMillis/schedule handler string
                      with no matching method definition (compiles clean, fails silently)
  missing-command     a declared `capability "X"` whose required command has no method
  install-trap        updated() wires subscriptions/schedules but installed() neither
                      calls updated() nor wires them itself (first-run does nothing)

Reference data (paths resolved relative to this script by default):
  ../_reference/allowed-imports.txt   one FQ class per line, '#' comments ignored
  ../_reference/capabilities.json     capability -> required commands

Usage:
    hub_lint.py SOURCE.groovy [--allowed-imports PATH] [--capabilities PATH]
    hub_lint.py --source-stdin < SOURCE.groovy

Output: JSON {source, kind, finding_count, findings:[{check, severity, line, symbol, message}]}
Exit 0 always on a successful lint (findings are data, not failure); non-zero only on
bad invocation or unreadable input.
"""
import argparse
import json
import re
import sys
from pathlib import Path

_REF = Path(__file__).resolve().parent.parent / "_reference"

# Command names that are lifecycle/util methods, never a capability-required command gap.
_LIFECYCLE = {"installed", "updated", "uninstalled", "initialize", "configure",
              "refresh", "poll", "parse", "installed", "updated"}


def strip_comments_and_strings(src: str, blank_strings: bool = True) -> str:
    """Blank out // and /* */ comments (always) and, when blank_strings is True, the
    contents of string literals too. Character positions and newlines are preserved
    exactly, so a position found in one rendering indexes the same spot in another and
    line numbers stay correct. Two renderings are used: the fully-blanked one to locate
    code (so `sleep` in a string is not a construct), and the comments-only one to read
    the string literals that ARE data (handler names, capability names)."""
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "//":
            j = src.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            chunk = src[i:j]
            out.append("".join(ch if ch == "\n" else " " for ch in chunk))
            i = j
        elif c in "\"'":
            triple = src[i:i + 3] in ('"""', "'''")
            delim = src[i:i + 3] if triple else c
            k = i + len(delim)
            end = src.find(delim, k)
            end = n if end == -1 else end
            body = src[k:end]
            if blank_strings:
                body = "".join(ch if ch == "\n" else " " for ch in body)
            out.append(delim + body)
            out.append("" if end == n else delim)
            i = (end + len(delim)) if end != n else n
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _lineno(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def _split_top_level(s: str) -> list:
    """Split an argument list on commas that are not nested inside (), [], or {}."""
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return parts


def _defined_methods(clean: str) -> set:
    # def name(  |  void name(  |  Type name(  — capture the method name
    names = set()
    for m in re.finditer(r'\b(?:def|void|private|public|static|[A-Za-z_][\w.<>]*)\s+([a-zA-Z_]\w*)\s*\(', clean):
        names.add(m.group(1))
    return names


def _load_allowed_imports(path: Path) -> set:
    allowed = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            allowed.add(line)
    return allowed


def _load_required_commands(path: Path) -> dict:
    data = json.loads(path.read_text())
    caps = data.get("capabilities", data)
    return {name: [c["name"] for c in cap.get("commands", [])] for name, cap in caps.items()}


def lint_source(source: str, allowed_imports: set, required_commands: dict) -> list:
    """Pure core. Returns a list of finding dicts sorted by line."""
    clean = strip_comments_and_strings(source)                       # comments + strings blanked
    literals = strip_comments_and_strings(source, blank_strings=False)  # comments blanked, strings intact
    findings = []

    def add(check, severity, pos, symbol, message):
        findings.append({"check": check, "severity": severity,
                         "line": _lineno(source, pos), "symbol": symbol, "message": message})

    # disallowed-import. Platform namespaces are always available; the documented
    # allow-list is known incomplete (real drivers import hubitat.* classes absent
    # from it), so a non-listed import is a WARN candidate for the skill to judge,
    # never a hard error. ([ \t]* not \s* — \s would slide the anchor across newlines.)
    for m in re.finditer(r'^[ \t]*import\s+(static\s+)?([\w.$*]+)', clean, re.M):
        fq = m.group(2)
        if fq.startswith(("hubitat.", "com.hubitat.")):
            continue  # platform-internal classes, always importable
        if fq.endswith(".*"):
            add("disallowed-import", "warn", m.start(), fq,
                f"Wildcard import '{fq}' cannot be verified against the allow-list; import the exact classes.")
        elif fq not in allowed_imports:
            add("disallowed-import", "warn", m.start(), fq,
                f"'{fq}' is not on the documented sandbox import allow-list (which is incomplete) — verify it compiles on the hub.")

    # forbidden-construct
    forbidden = [
        (r'\bsleep\s*\(', "sleep()", "use pauseExecution(ms)"),
        (r'\bprintln\b', "println", "use log.debug"),
        (r'\bnew\s+Thread\b', "new Thread", "threads are not allowed in the sandbox"),
        (r'\bExecutors\s*\.', "Executors", "thread pools are not allowed in the sandbox"),
        (r'\.getClass\s*\(', ".getClass()", "use getObjectClassName(obj)"),
    ]
    for pat, sym, fix in forbidden:
        for m in re.finditer(pat, clean):
            add("forbidden-construct", "error", m.start(), sym,
                f"{sym} is blocked by the sandbox — {fix}.")

    methods = _defined_methods(clean)

    # unresolved-handler — the handler is the rightmost POSITIONAL string argument,
    # skipping any trailing options map (e.g. [overwrite: true, misfire: "ignore"]) or
    # named argument. A bare (unquoted) method reference in that position is left to the
    # compiler — only a quoted handler name is checkable here.
    handler_calls = r'\b(subscribe|runIn|runInMillis|runOnce|schedule)\s*\('
    for m in re.finditer(handler_calls, clean):
        start = m.end()
        depth, j = 1, start
        while j < len(clean) and depth:
            if clean[j] == "(":
                depth += 1
            elif clean[j] == ")":
                depth -= 1
            j += 1
        args = literals[start:j - 1]  # strings intact — the handler name is a literal
        handler = None
        for part in reversed(_split_top_level(args)):
            if not part or part.startswith("[") or ":" in part:
                continue  # options map or named arg
            hm = re.fullmatch(r'["\']([A-Za-z_]\w*)["\']', part)
            if hm:
                handler = hm.group(1)
            break  # first non-map positional from the right decides
        if handler and handler not in methods:
            add("unresolved-handler", "error", m.start(), handler,
                f"{m.group(1)}(...) references handler \"{handler}\" but no method by that name is defined.")

    # missing-command (capability name is a string literal — read from `literals`)
    for m in re.finditer(r'\bcapability\s+["\']([A-Za-z0-9]+)["\']', literals):
        cap = m.group(1)
        for cmd in required_commands.get(cap, []):
            if cmd and cmd not in methods and cmd not in _LIFECYCLE:
                add("missing-command", "error", m.start(), f"{cap}.{cmd}",
                    f"capability \"{cap}\" requires command {cmd}() but no such method is defined.")

    # install-trap (apps)
    upd = re.search(r'\bdef\s+updated\s*\(', clean)
    inst = re.search(r'\bdef\s+installed\s*\(', clean)
    if upd and inst:
        def _body(match):
            start = clean.find("{", match.end())
            if start == -1:
                return ""
            depth, j = 1, start + 1
            while j < len(clean) and depth:
                if clean[j] == "{":
                    depth += 1
                elif clean[j] == "}":
                    depth -= 1
                j += 1
            return clean[start:j]
        wires = re.compile(r'\b(subscribe|runIn|runInMillis|runOnce|schedule|initialize)\s*\(')
        upd_body, inst_body = _body(upd), _body(inst)
        if wires.search(upd_body) and "updated" not in inst_body and not wires.search(inst_body):
            add("install-trap", "warn", inst.start(), "installed",
                "installed() neither calls updated() nor wires subscriptions/schedules — on first install the app does nothing until the second Done.")

    findings.sort(key=lambda f: (f["line"], f["check"]))
    return findings


def _classify(source: str) -> str:
    if re.search(r'\bmetadata\s*\{', source):
        return "driver"
    if re.search(r'\bpreferences\s*\{', source) or re.search(r'\bdefinition\s*\(', source):
        return "app"
    return "unknown"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Lint Hubitat Groovy for sandbox violations.")
    p.add_argument("source", nargs="?", help="path to .groovy source (omit with --source-stdin)")
    p.add_argument("--source-stdin", action="store_true", help="read source from stdin")
    p.add_argument("--allowed-imports", default=str(_REF / "allowed-imports.txt"))
    p.add_argument("--capabilities", default=str(_REF / "capabilities.json"))
    args = p.parse_args(argv)

    if args.source_stdin:
        source = sys.stdin.read()
        name = "<stdin>"
    elif args.source:
        try:
            source = Path(args.source).read_text()
        except OSError as e:
            print(f"cannot read {args.source}: {e}", file=sys.stderr)
            return 2
        name = args.source
    else:
        print("provide a source path or --source-stdin", file=sys.stderr)
        return 2

    try:
        allowed = _load_allowed_imports(Path(args.allowed_imports))
        required = _load_required_commands(Path(args.capabilities))
    except (OSError, json.JSONDecodeError) as e:
        print(f"cannot read reference data: {e}", file=sys.stderr)
        return 2

    findings = lint_source(source, allowed, required)
    result = {"source": name, "kind": _classify(source),
              "finding_count": len(findings), "findings": findings}
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
