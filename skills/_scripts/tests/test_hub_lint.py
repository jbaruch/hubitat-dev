#!/usr/bin/env python3
"""Tests for skills/_scripts/hub_lint.py — the sandbox/silent-failure linter."""

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "hub_lint.py"
spec = importlib.util.spec_from_file_location("hub_lint", SCRIPT)
assert spec and spec.loader, f"cannot load hub_lint.py at {SCRIPT}"
hub_lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hub_lint)

# Minimal reference data, built programmatically (no external files, deterministic).
ALLOWED = {"java.util.ArrayList", "groovy.json.JsonSlurper", "groovy.transform.Field"}
REQUIRED = {"Switch": ["off", "on"], "SwitchLevel": ["setLevel"], "Actuator": []}


def checks(findings):
    return sorted(f["check"] for f in findings)


def by_check(findings, check):
    return [f for f in findings if f["check"] == check]


class TestCleanSource(unittest.TestCase):
    def test_correct_switch_driver_has_no_findings(self):
        src = (
            'metadata {\n'
            '  definition(name: "X", namespace: "n", author: "a") {\n'
            '    capability "Actuator"\n'
            '    capability "Switch"\n'
            '  }\n'
            '}\n'
            'def on()  { sendEvent(name: "switch", value: "on") }\n'
            'def off() { sendEvent(name: "switch", value: "off") }\n'
        )
        self.assertEqual(hub_lint.lint_source(src, ALLOWED, REQUIRED), [])


class TestDisallowedImport(unittest.TestCase):
    def test_flags_class_not_on_allowlist_as_warning(self):
        # The documented allow-list is incomplete, so a non-listed import is a WARN
        # candidate, not a hard error.
        src = 'import java.lang.Thread\nimport java.util.ArrayList\n'
        f = by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "disallowed-import")
        self.assertEqual([x["symbol"] for x in f], ["java.lang.Thread"])
        self.assertEqual(f[0]["severity"], "warn")
        self.assertEqual(f[0]["line"], 1)

    def test_platform_namespace_imports_are_allowed(self):
        # Real drivers import these and compile; the docs list omits them.
        src = ('import hubitat.device.HubAction\n'
               'import hubitat.helper.HexUtils\n'
               'import com.hubitat.app.DeviceWrapper\n')
        self.assertEqual(by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "disallowed-import"), [])

    def test_wildcard_import_is_a_warning(self):
        src = 'import java.util.*\n'
        f = by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "disallowed-import")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["severity"], "warn")


class TestForbiddenConstruct(unittest.TestCase):
    def test_flags_sleep_println_thread_getclass(self):
        src = (
            'def f() {\n'
            '  sleep(1000)\n'
            '  println "hi"\n'
            '  new Thread().start()\n'
            '  def c = obj.getClass()\n'
            '}\n'
        )
        syms = sorted(x["symbol"] for x in by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "forbidden-construct"))
        self.assertEqual(syms, [".getClass()", "new Thread", "println", "sleep()"])

    def test_pauseExecution_is_not_flagged(self):
        src = 'def f() { pauseExecution(500) }\n'
        self.assertEqual(by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "forbidden-construct"), [])


class TestUnresolvedHandler(unittest.TestCase):
    def test_flags_missing_handler_method(self):
        src = (
            'def updated() {\n'
            '  subscribe(sensor, "motion", "motionHandler")\n'
            '  runIn(300, "checkState")\n'
            '}\n'
            'def motionHandler(evt) { }\n'  # checkState is missing
        )
        f = by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "unresolved-handler")
        self.assertEqual([x["symbol"] for x in f], ["checkState"])

    def test_resolved_handlers_pass(self):
        src = (
            'def updated() { subscribe(s, "switch", "onEvt") }\n'
            'def onEvt(evt) { }\n'
        )
        self.assertEqual(by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "unresolved-handler"), [])

    def test_options_map_value_is_not_mistaken_for_handler(self):
        # "ignore" is a misfire option value, not the handler; poll() is defined.
        src = (
            'def updated() { runIn(300, "poll", [overwrite: true, misfire: "ignore"]) }\n'
            'def poll() { }\n'
        )
        self.assertEqual(by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "unresolved-handler"), [])

    def test_bare_unquoted_handler_reference_is_left_to_compiler(self):
        # Handler passed as a bare method reference — the attribute string must not be flagged.
        src = 'def updated() { subscribe(loc, "mode", modeHandler) }\n'
        self.assertEqual(by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "unresolved-handler"), [])


class TestMissingCommand(unittest.TestCase):
    def test_flags_capability_without_command_method(self):
        src = (
            'metadata { definition(name:"X",namespace:"n",author:"a") {\n'
            '  capability "Switch"\n'
            '} }\n'
            'def on() { }\n'  # off() missing
        )
        f = by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "missing-command")
        self.assertEqual([x["symbol"] for x in f], ["Switch.off"])

    def test_marker_capability_needs_no_command(self):
        src = 'metadata { definition(name:"X",namespace:"n",author:"a") { capability "Actuator" } }\n'
        self.assertEqual(by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "missing-command"), [])


class TestInstallTrap(unittest.TestCase):
    def test_flags_updated_wires_but_installed_empty(self):
        src = (
            'def installed() { log.info "hi" }\n'
            'def updated() { unsubscribe(); subscribe(s, "switch", "h") }\n'
            'def h(evt) { }\n'
        )
        self.assertEqual(len(by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "install-trap")), 1)

    def test_installed_calling_updated_is_safe(self):
        src = (
            'def installed() { updated() }\n'
            'def updated() { subscribe(s, "switch", "h") }\n'
            'def h(evt) { }\n'
        )
        self.assertEqual(by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "install-trap"), [])


class TestStripping(unittest.TestCase):
    def test_forbidden_words_in_comments_and_strings_are_ignored(self):
        src = (
            'def f() {\n'
            '  // this sleep(1) is a comment, println too\n'
            '  def msg = "call sleep(9) in text, not code"\n'
            '  log.debug "println is fine here"\n'
            '}\n'
        )
        self.assertEqual(by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "forbidden-construct"), [])

    def test_import_after_block_comment_still_detected(self):
        src = '/* header\n spanning lines */\nimport java.lang.Thread\n'
        f = by_check(hub_lint.lint_source(src, ALLOWED, REQUIRED), "disallowed-import")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["line"], 3)


class TestMainErrorPaths(unittest.TestCase):
    def test_malformed_capabilities_returns_nonzero(self):
        import io
        import tempfile
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as d:
            from pathlib import Path as _P
            src = _P(d) / "d.groovy"
            src.write_text("def on() {}")
            bad = _P(d) / "caps.json"
            bad.write_text("{ not json")
            allow = _P(d) / "allow.txt"
            allow.write_text("java.util.ArrayList\n")
            err = io.StringIO()
            with redirect_stderr(err):
                rc = hub_lint.main([str(src), "--capabilities", str(bad), "--allowed-imports", str(allow)])
            self.assertEqual(rc, 2)
            self.assertIn("reference data", err.getvalue())


if __name__ == "__main__":
    unittest.main()
