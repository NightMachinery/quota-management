"""Offline synthetic fixtures only. No real account usage or authentication.

python3 -m unittest discover -s skills/quota-management/scripts -p 'test_*.py'
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import quota_readers as readers
import quota_watch as watch

SCRIPTS = Path(__file__).resolve().parent
RESET = 2000000000  # Synthetic epoch used by all fixtures.


def account(name="account-a", used=60, *, error=None, bucket="model-5h"):
    return {"account": name, "signed_in": not bool(error), "error": error,
            "auth_status": "file-present", "private_identity": "discard-me",
            "buckets": [] if error else [{"bucket_id": bucket, "used_percent": used,
                                          "resets_at": RESET, "source": "cache"}]}


def signed_out(name):
    return {"account": name, "auth_status": "missing", "signed_in": False}


class QuotaWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        locations = patch.multiple(
            watch, BASE=self.root, LOGDIR=self.root / "logs", ALERTDIR=self.root / "alerts",
            STATE_MD=self.root / "STATE.md", LOGFILE=self.root / "logs/watch.log",
            STATEFILE=self.root / "logs/watch.state", FAILFILE=self.root / "logs/watch.fail")
        locations.start()
        self.addCleanup(locations.stop)
        environ = patch.dict(os.environ, {"QUOTA_WATCH_BASE": str(self.root),
                                          "QUOTA_WATCH_AUTO": "0", "QUOTA_WATCH_READERS": "{}"})
        environ.start()
        self.addCleanup(environ.stop)

    def parse(self, rows, format_name="agy", rc=0):
        result = subprocess.CompletedProcess([], rc, json.dumps(rows), "private stderr")
        with patch.object(readers.subprocess, "run", return_value=result):
            return readers.measure(format_name, (["fixture"], format_name))

    def command(self, data, rc=0):
        script = self.root / ("reader%d.py" % len(list(self.root.glob("reader*.py"))))
        script.write_text("import json, sys\nprint(" + repr(json.dumps(data)) + ")\nsys.exit(%d)\n" % rc)
        return [sys.executable, str(script)]

    def test_pluggable_reader_command_and_opaque_fields(self):
        obj = {"gauges": {"account-a.5h": {"pct": 42, "reset": RESET}},
               "private_identity": "discard-me"}
        command = self.command(obj)
        os.environ["QUOTA_WATCH_READERS"] = json.dumps({"provider": command})
        mode, configured = readers.choose_readers()
        self.assertEqual(mode, "generic")
        self.assertEqual(configured["provider"], (command, "normalized"))
        self.assertEqual(watch.cycle(), 0)
        text = watch.STATE_MD.read_text()
        self.assertIn("Gauge provider.account-a.5h: 42% used", text)
        for path in watch.LOGDIR.iterdir():
            self.assertNotIn("discard-me", path.read_text())
        self.assertIn("readers: generic (provider)", watch.LOGFILE.read_text())

    def test_no_reader_marks_remembered_data_stale(self):
        watch.write_state({"old.5h": {"pct": 65, "reset": RESET}})
        self.assertEqual(watch.cycle(), 2)
        self.assertIn("no reader configured", watch.STATE_MD.read_text())
        self.assertIn("65%~", watch.STATE_MD.read_text())
        self.assertFalse(list(watch.ALERTDIR.iterdir()))

    def test_invalid_configuration(self):
        for raw in ["bad", "[]", '{"bad/source":["cmd"]}', '{"source":"cmd"}', '{"source":[]}']:
            with self.subTest(raw=raw), patch.dict(os.environ, {"QUOTA_WATCH_READERS": raw}):
                self.assertEqual(watch.cycle(), 2)

    def test_normalized_invalid_numbers_and_status_conflicts(self):
        for bad in [float("nan"), float("inf"), -1, 101, "45", True, None]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    readers.normalize("provider", {"gauges": {"5h": {"pct": bad}}})
        for reset in [float("inf"), -1, "100", True]:
            with self.assertRaises(ValueError):
                readers.normalize("provider", {"gauges": {"5h": {"pct": 30, "reset": reset}}})
        with self.assertRaises(ValueError):
            readers.normalize("provider", {"gauges": {"account-a.5h": {"pct": 30}},
                                            "signed_out": ["account-a"]})

    def test_all_accounts_and_private_fields_discarded(self):
        gauges, errors, inactive = self.parse([
            account(), account("account-b", 87), account("account-c", error="discard-me")])
        self.assertEqual(gauges["agy.account-b.model-5h"]["pct"], 87)
        self.assertEqual(set(errors), {"agy.account-c"})
        self.assertNotIn("discard-me", json.dumps([gauges, errors]))
        self.assertFalse(inactive)

    def test_signed_out_accounts_normal(self):
        gauges, errors, inactive = self.parse([account(), signed_out("account-b")])
        self.assertEqual(set(gauges), {"agy.account-a.model-5h"})
        self.assertFalse(errors)
        self.assertEqual(inactive, {"agy.account-b"})

    def test_general_and_model_buckets_coexist(self):
        row = account()
        row["buckets"] = [dict(row["buckets"][0], bucket_id=bucket, used_percent=20 + index)
                          for index, bucket in enumerate(["general-5h", "general-weekly", "model-5h", "model-weekly"])]
        gauges, errors, _ = self.parse([row])
        self.assertEqual(len(gauges), 4)
        self.assertFalse(errors)
        self.assertEqual(len(set(v["pct"] for v in gauges.values())), 4)

    def test_remaining_percent_and_bucket_path_escaping(self):
        row = account(bucket="group/weekly|x")
        row["buckets"][0].pop("used_percent")
        row["buckets"][0]["remaining_percent"] = 17.5
        gauges, errors, _ = self.parse([row])
        self.assertFalse(errors)
        self.assertEqual(gauges["agy.account-a.group%2Fweekly%7Cx"]["pct"], 82)
        self.assertEqual(watch.diff_alerts({}, {"provider...%2F": {"pct": 95, "reset": RESET}}), 1)
        self.assertEqual(len(list(watch.ALERTDIR.iterdir())), 1)

    def test_invalid_bucket_does_not_hide_other_account(self):
        for bad in [float("nan"), float("inf"), -1, 101, "bad"]:
            gauges, errors, _ = self.parse([account(used=bad), account("account-b", 40)])
            self.assertEqual(set(gauges), {"agy.account-b.model-5h"})
            self.assertIn("agy.account-a", errors)
        row = account()
        row["buckets"][0]["source"] = "live"
        self.assertIn("agy.account-a", self.parse([row])[1])

    def test_night_partial_nonzero_accepted_generic_nonzero_rejected(self):
        self.assertIn("agy.account-a.model-5h", self.parse([account()], rc=1)[0])
        result = subprocess.CompletedProcess([], 1, '{"gauges":{"5h":{"pct":35}}}', "")
        with patch.object(readers.subprocess, "run", return_value=result):
            self.assertIn("provider", readers.measure("provider", (["fixture"], "normalized"))[1])

    def test_malformed_or_timeout_produces_sanitized_error(self):
        for obj in [[], {}, [None], [account("../bad")]]:
            self.assertIn("agy", self.parse(obj)[1])
        with patch.object(readers.subprocess, "run", side_effect=subprocess.TimeoutExpired(["private"], 1)):
            self.assertEqual(readers.measure("source", (["fixture"], "normalized"))[1],
                             {"source": "reader failed or returned invalid JSON; check manually"})

    def test_claude_adapter_windows_and_stale_cache(self):
        rows = [{"profile": "account-a", "windows": [{"key": "session", "utilization_percent": 35,
                  "resets_at": RESET}, {"key": "weekly_scoped", "utilization_percent": 15}]}]
        gauges, errors, _ = self.parse(rows, "claude")
        self.assertEqual(gauges["claude.account-a.5h"]["pct"], 35)
        self.assertIn("claude.account-a.model7d", gauges)
        self.assertFalse(errors)
        rows[0]["cache"] = {"stale_reason": "expired"}
        self.assertIn("claude.account-a", self.parse(rows, "claude")[1])

    def test_codex_durations_multiple_pools_and_missing_windows(self):
        obj = {"rateLimitsByLimitId": {
            "pool-a": {"primary": {"usedPercent": 30, "windowDurationMins": 10080}, "secondary": None},
            "pool-b": {"primary": {"usedPercent": 40, "windowDurationMins": 300},
                       "secondary": {"usedPercent": 50, "windowDurationMins": 60}}}}
        gauges, errors, _ = self.parse(obj, "codex")
        self.assertEqual(set(gauges), {"codex.account1.pool-a.7d", "codex.account1.pool-b.5h",
                                      "codex.account1.pool-b.60min"})
        self.assertFalse(errors)
        old = {"authFiles": [{"ok": True, "authFile": "discard-me", "rateLimits": {
            "primary": {"usedPercent": 35}}}]}
        self.assertEqual(set(self.parse(old, "codex")[0]), {"codex.account1.codex.primary"})

    def test_thresholds_reset_restart_and_no_alert_churn(self):
        gauge = "provider.account-a.5h"
        current = lambda n, reset=RESET: {gauge: {"pct": n, "reset": reset}}
        self.assertEqual(watch.diff_alerts({}, current(70)), 0)
        for old, new in [(69, 70), (70, 85), (85, 90), (90, 95), (95, 100)]:
            self.assertEqual(watch.diff_alerts(current(old), current(new)), 1)
        watch.write_state(current(95))
        self.assertEqual(watch.read_state(), current(95))
        self.assertEqual(watch.diff_alerts(watch.read_state(), current(95)), 0)
        self.assertEqual(watch.diff_alerts(current(95), current(0)), 1)
        self.assertEqual(watch.diff_alerts(current(0), current(0, RESET + 1000)), 0)
        self.assertEqual(watch.diff_alerts(current(10), current(10, RESET + 30)), 0)

    def test_partial_failure_retains_stale_notes_and_other_sections(self):
        old = {"agy.account-b.model-5h": {"pct": 91, "reset": RESET}}
        watch.write_state(old)
        watch.STATE_MD.write_text("# Run\n## Quota\nPlan: keep this\nGauge old: remove this\n## Jobs\njob\n")
        data = [account(), account("account-b", error="discard-me")]
        selected = ("night", {"agy": (["fixture"], "agy")})
        with patch.object(watch, "choose_readers", return_value=selected), \
             patch.object(watch, "measure", return_value=self.parse(data)):
            self.assertEqual(watch.cycle(), 1)
            self.assertEqual(watch.cycle(), 1)
        text = watch.STATE_MD.read_text()
        self.assertIn("agy.account-b.model-5h: 91%~", text)
        self.assertIn("agy.account-a.model-5h: 60% used", text)
        self.assertIn("Reader agy.account-b: unavailable", text)
        self.assertIn("Plan: keep this\n", text)
        self.assertIn("## Jobs\njob", text)
        self.assertNotIn("Gauge old:", text)
        self.assertEqual(len(list(watch.ALERTDIR.glob("*agy.account-b-error.md"))), 1)
        self.assertEqual(watch.read_state()["agy.account-b.model-5h"], old["agy.account-b.model-5h"])

    def test_total_outage_updates_stale_state(self):
        watch.write_state({"provider.5h": {"pct": 55, "reset": RESET}})
        selected = ("generic", {"provider": (["fixture"], "normalized")})
        with patch.object(watch, "choose_readers", return_value=selected), \
             patch.object(watch, "measure", return_value=({}, {"provider": "unavailable"}, set())):
            self.assertEqual(watch.cycle(), 1)
        self.assertIn("55%~", watch.STATE_MD.read_text())

    def test_signed_out_clears_baseline_and_failure_count(self):
        watch.write_state({"agy.account-b.model-5h": {"pct": 99, "reset": RESET}})
        watch.write_fails({"agy.account-b": 7})
        selected = ("night", {"agy": (["fixture"], "agy")})
        with patch.object(watch, "choose_readers", return_value=selected), \
             patch.object(watch, "measure", return_value=self.parse([signed_out("account-b")])):
            self.assertEqual(watch.cycle(), 0)
        self.assertIn("agy.account-b: not signed in", watch.STATE_MD.read_text())
        self.assertEqual(watch.read_state(), {})
        self.assertEqual(watch.read_fails()["agy.account-b"], 0)
        self.assertEqual(list(watch.ALERTDIR.iterdir()), [])

    def test_repeated_failure_cadence(self):
        fails = {}
        for _ in range(16):
            watch.source_failure("source", "unavailable", fails)
        self.assertEqual(len(list(watch.ALERTDIR.iterdir())), 3)

    def test_night_detection_precedence_and_generic_fill(self):
        with patch.dict(os.environ, {"QUOTA_WATCH_AUTO": "1", "QUOTA_WATCH_TOOLING": "night",
                                    "QUOTA_WATCH_READERS": '{"claude":["custom"],"extra":["reader"]}'}), \
             patch.object(readers.shutil, "which", side_effect=lambda cmd: "/bin/" + cmd if cmd == "zsh" else None), \
             patch.object(readers.Path, "is_file", return_value=False):
            mode, chosen = readers.choose_readers()
        self.assertEqual(mode, "night")
        self.assertEqual(chosen["extra"], (["reader"], "normalized"))
        self.assertEqual(chosen["claude"][1], "claude")
        self.assertIn("whence claude-code-usage-all", chosen["claude"][0][2])
        self.assertIn("--no-relogin", chosen["claude"][0][2])

    def test_generic_auto_detection(self):
        with patch.dict(os.environ, {"QUOTA_WATCH_AUTO": "1", "QUOTA_WATCH_TOOLING": "generic",
                                    "QUOTA_WATCH_CLAUDE_CACHE": "fixture.json"}), \
             patch.object(readers.Path, "is_file", return_value=False), \
             patch.object(readers.shutil, "which", side_effect=lambda cmd: "/bin/codex" if cmd == "codex" else None):
            mode, chosen = readers.choose_readers()
        self.assertEqual(mode, "generic")
        self.assertEqual(set(chosen), {"claude", "codex"})
        self.assertEqual(chosen["codex"][0][-1], "codex")

    def test_claude_statusline_cache_only_quota_and_freshness(self):
        cache = self.root / "cache.json"
        payload = {"transcript_path": "discard-me", "rate_limits": {
            "five_hour": {"used_percentage": 23.5, "resets_at": RESET},
            "seven_day": {"used_percentage": 41, "resets_at": RESET}}}
        readers.capture_claude(cache, payload)
        self.assertNotIn("discard-me", cache.read_text())
        self.assertEqual(readers.cache_read(cache)["gauges"]["5h"]["pct"], 24)
        before = cache.read_text()
        readers.capture_claude(cache, {})
        self.assertEqual(cache.read_text(), before)
        os.utime(cache, (time.time() - 3600, time.time() - 3600))
        with self.assertRaises(ValueError):
            readers.cache_read(cache)

    def test_codex_native_handshake_with_stub_no_turn(self):
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "codex"
        requests = self.root / "requests.jsonl"
        stub.write_text("#!" + sys.executable + "\n" + '''import json, os, sys
assert sys.argv[1:] == ['app-server']
for line in sys.stdin:
    msg = json.loads(line)
    with open(os.environ['FIXTURE_REQUESTS'], 'a') as out:
        out.write(line)
    if msg['method'] == 'initialize':
        print(json.dumps({'id': msg['id'], 'result': {}}), flush=True)
    elif msg['method'] == 'initialized':
        continue
    elif msg['method'] == 'account/rateLimits/read':
        print(json.dumps({'id': msg['id'], 'result': {'rateLimits': {'primary': {
            'usedPercent': 35, 'windowDurationMins': 300}}}}), flush=True)
    else:
        raise AssertionError(msg['method'])
''')
        stub.chmod(0o700)
        with patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                                    "FIXTURE_REQUESTS": str(requests)}):
            result = readers.codex_native()
        self.assertEqual(result["gauges"]["account1.codex.5h"]["pct"], 35)
        self.assertEqual([json.loads(line)["method"] for line in requests.read_text().splitlines()],
                         ["initialize", "initialized", "account/rateLimits/read"])

    def test_shell_once_and_no_reader_loop_stop(self):
        obj = {"gauges": {"5h": {"pct": 42, "reset": RESET}}}
        env = {**os.environ, "QUOTA_WATCH_READERS": json.dumps({"provider": self.command(obj)})}
        for _ in range(2):
            result = subprocess.run(["bash", str(SCRIPTS / "quota-watch.sh"), "--once"],
                                    env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list((self.root / "alerts").iterdir()), [])
        env["QUOTA_WATCH_READERS"] = "{}"
        result = subprocess.run(["bash", str(SCRIPTS / "quota-watch.sh")], env=env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no reader configured", result.stdout)

    def test_missing_base_has_no_side_effect(self):
        env = {k: v for k, v in os.environ.items() if k != "QUOTA_WATCH_BASE"}
        result = subprocess.run(["bash", str(SCRIPTS / "quota-watch.sh"), "--once"],
                                env=env, cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(list(self.root.iterdir()))


if __name__ == "__main__":
    unittest.main()
