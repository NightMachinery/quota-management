"""Reader selection, adapters and a small public JSON contract.

No provider payload or stderr is persisted. Only normalized gauges survive.
"""
from __future__ import annotations
import json
import math
import os
import queue
import re
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
NAME = re.compile(r"[A-Za-z0-9_-]+")


def tooling():
    if os.environ.get("QUOTA_WATCH_AUTO", "1") == "0":
        return "generic"
    if os.environ.get("QUOTA_WATCH_TOOLING") == "night":
        return "night"
    return "night" if any((Path.home() / p).is_file() for p in (
        "scripts/python/claude_code_usage.py", "scripts/python/codex_status.py")) or any(
            shutil.which(p) for p in ("claude-code-usage-all", "codex-status")) else "generic"


def choose_readers():
    mode = tooling()
    raw = os.environ.get("QUOTA_WATCH_READERS", "{}")
    try:
        custom = json.loads(raw)
        if not isinstance(custom, dict):
            raise ValueError()
        for source, argv in custom.items():
            if not NAME.fullmatch(source) or not isinstance(argv, list) or not argv or not all(
                    isinstance(arg, str) and arg for arg in argv):
                raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("invalid reader configuration") from None
    readers = {}
    if mode == "night":
        # Prefer shell helpers if defined, otherwise the existing Python reader.
        # No named account, implicit profile directory or login refresh here.
        for source, helper, script, args in (
            ("claude", "claude-code-usage-all", "claude_code_usage.py", "--all --json --no-relogin"),
            ("codex", "codex-status", "codex_status.py", "--json")):
            path = Path.home() / "scripts/python" / script
            if path.is_file() or shutil.which(helper) or os.environ.get("QUOTA_WATCH_TOOLING") == "night":
                if shutil.which("zsh"):
                    helper_args = "--json --no-relogin" if source == "claude" else "--json"
                    snippet = ('if whence %s >/dev/null 2>&1; then %s %s; '
                               'else command python3 %s %s; fi') % (
                                   helper, helper, helper_args, shlex.quote(str(path)), args)
                    readers[source] = (["zsh", "-ic", snippet], source)
                else:
                    readers[source] = ([sys.executable, str(path), *args.split()], source)
        if shutil.which("agy-status") or (Path.home() / "scripts/zshlang/auto-load/others/agy-status.zsh").is_file():
            if shutil.which("zsh"):
                readers["agy"] = (["zsh", "-ic", "agy_status_method=statusline agy_status_cache_only_p=y agy-status --json"], "agy")
    # Explicit generic commands fill missing providers. Night wins duplicates.
    for source, argv in custom.items():
        readers.setdefault(source, (argv, "normalized"))
    if os.environ.get("QUOTA_WATCH_AUTO", "1") != "0":
        if "codex" not in readers and shutil.which("codex"):
            readers["codex"] = ([sys.executable, str(HERE / "quota_readers.py"), "codex"], "normalized")
        cache = os.environ.get("QUOTA_WATCH_CLAUDE_CACHE")
        if "claude" not in readers and cache:
            readers["claude"] = ([sys.executable, str(HERE / "quota_readers.py"), "claude-cache", cache], "normalized")
    return mode, readers


def value(pct, reset=None):
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        raise ValueError("invalid percentage")
    if not math.isfinite(pct) or not 0 <= pct <= 100:
        raise ValueError("invalid percentage")
    if reset is not None and (isinstance(reset, bool) or not isinstance(reset, (int, float))
                              or not math.isfinite(reset) or reset < 0):
        raise ValueError("invalid reset")
    return {"pct": int(round(pct)), "reset": reset}


def label(name):
    if not isinstance(name, str) or not name or len(name) > 180:
        raise ValueError("invalid gauge identifier")
    return name


def identifier(name):
    if not isinstance(name, str) or not name or len(name) > 180:
        raise ValueError("invalid gauge identifier")
    # Dots separate scopes. Encode path separators and line breaks for filenames.
    return quote(name, safe=".-_")


def normalize(source, obj):
    if not isinstance(obj, dict) or not isinstance(obj.get("gauges"), dict):
        raise ValueError("expected gauges object")
    gauges = {}
    for key, row in obj["gauges"].items():
        if not isinstance(row, dict):
            raise ValueError("invalid gauge")
        gauges[source + "." + identifier(key)] = value(row.get("pct"), row.get("reset"))
    errors, inactive = {}, set()
    for field in ("unavailable", "signed_out"):
        entries = obj.get(field, [])
        if not isinstance(entries, list):
            raise ValueError("expected status list")
        for key in entries:
            scoped = source + "." + identifier(key)
            if field == "unavailable":
                errors[scoped] = "reader unavailable; check manually"
            else:
                inactive.add(scoped)
    for key in gauges:
        if any(key == s or key.startswith(s + ".") for s in set(errors) | inactive):
            raise ValueError("conflicting gauge status")
    if set(errors) & inactive:
        raise ValueError("conflicting source status")
    return gauges, errors, inactive


def claude_rows(obj):
    if not isinstance(obj, list) or not obj:
        raise ValueError("expected profile list")
    out = {"gauges": {}, "unavailable": []}
    suffixes = {"session": "5h", "weekly_all": "7d", "weekly_scoped": "model7d",
                "five_hour": "5h", "seven_day": "7d"}
    for row in obj:
        profile = label(row.get("profile") or "account")
        if row.get("error") or (row.get("cache") or {}).get("stale_reason"):
            out["unavailable"].append(profile)
            continue
        found = False
        for window in row.get("windows") or []:
            if window.get("utilization_percent") is None:
                continue
            suffix = suffixes.get(window.get("key"), label(window.get("key")))
            out["gauges"][profile + "." + suffix] = value(
                window["utilization_percent"], window.get("resets_at"))
            found = True
        if not found:
            out["unavailable"].append(profile)
    return out


def codex_rows(obj):
    if not isinstance(obj, dict):
        raise ValueError("expected rate-limit object")
    out = {"gauges": {}, "unavailable": []}
    entries = obj.get("authFiles")
    if entries is None:
        entries = [{"ok": True, **obj}]
    if not isinstance(entries, list) or not entries:
        raise ValueError("expected account rows")
    for index, row in enumerate(entries):
        # Deliberately opaque local aliases, never auth paths or email addresses.
        account = "account%d" % (index + 1)
        if not row.get("ok"):
            out["unavailable"].append(account)
            continue
        pools = row.get("rateLimitsByLimitId") or {"codex": row.get("rateLimits")}
        found = False
        for pool, limits in pools.items():
            if not isinstance(limits, dict):
                continue
            for slot in ("primary", "secondary"):
                window = limits.get(slot)
                if not isinstance(window, dict) or window.get("usedPercent") is None:
                    continue
                duration = window.get("windowDurationMins")
                # Never infer window length from primary/secondary slot order.
                suffix = {300: "5h", 10080: "7d"}.get(duration, slot if duration is None else str(duration) + "min")
                name = account + "." + label(pool) + "." + suffix
                if name in out["gauges"]:
                    name += "." + slot
                out["gauges"][name] = value(window["usedPercent"], window.get("resetsAt"))
                found = True
        if not found:
            out["unavailable"].append(account)
    return out


def agy_rows(obj):
    if not isinstance(obj, list) or not obj:
        raise ValueError("expected cached accounts")
    out = {"gauges": {}, "unavailable": [], "signed_out": []}
    seen = set()
    for row in obj:
        account = row.get("account")
        if not isinstance(account, str) or not NAME.fullmatch(account) or account in seen:
            raise ValueError("invalid account")
        seen.add(account)
        if row.get("auth_status") == "missing" and row.get("signed_in") is False:
            out["signed_out"].append(account)
            continue
        if row.get("error") or row.get("signed_in") is False or not row.get("buckets"):
            out["unavailable"].append(account)
            continue
        account_gauges = {}
        try:
            for bucket in row["buckets"]:
                if bucket.get("source") != "cache":
                    raise ValueError("cache required")
                name = account + "." + label(bucket.get("bucket_id"))
                pct = bucket.get("used_percent")
                if pct is None:
                    pct = 100 - float(bucket["remaining_percent"])
                if name in account_gauges:
                    raise ValueError("duplicate bucket")
                account_gauges[name] = value(pct, bucket.get("resets_at"))
            out["gauges"].update(account_gauges)
        except (KeyError, TypeError, ValueError):
            out["unavailable"].append(account)
    return out


def measure(source, reader):
    argv, format_name = reader
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                                env={**os.environ, "NO_COLOR": "1"})
        obj = json.loads(result.stdout)
        if format_name == "normalized" and result.returncode:
            raise ValueError("reader failed")
        # Night multi-account commands can return valid partial rows with rc=1.
        adapter = {"claude": claude_rows, "codex": codex_rows, "agy": agy_rows}.get(format_name)
        return normalize(source, adapter(obj) if adapter else obj)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, subprocess.TimeoutExpired):
        return {}, {source: "reader failed or returned invalid JSON; check manually"}, set()


def codex_native():
    """Documented app-server handshake and quota read, without starting a turn."""
    with tempfile.TemporaryDirectory(prefix="quota-reader-") as scratch:
        proc = subprocess.Popen(["codex", "app-server"], cwd=scratch, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        messages = queue.Queue()
        def pump():
            for line in proc.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    continue
            messages.put(None)
        thread = threading.Thread(target=pump, daemon=True)
        thread.start()
        deadline = time.monotonic() + 45
        def send(message):
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        def response(number):
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("reader timeout")
                try:
                    msg = messages.get(timeout=remaining)
                except queue.Empty:
                    raise ValueError("reader timeout") from None
                if msg is None:
                    raise ValueError("reader closed")
                if msg.get("id") == number:
                    if "error" in msg:
                        raise ValueError("reader unavailable")
                    return msg["result"]
        try:
            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "quota_watcher", "version": "1.0.0"}}})
            response(1)
            send({"method": "initialized", "params": {}})
            send({"id": 2, "method": "account/rateLimits/read"})
            return codex_rows(response(2))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            thread.join(timeout=1)
            proc.stdin.close()
            proc.stdout.close()


def cache_read(path):
    path = Path(path).expanduser()
    age = time.time() - path.stat().st_mtime
    if age < -60 or age > int(os.environ.get("QUOTA_WATCH_CACHE_MAX_AGE", "1800")):
        raise ValueError("statusline cache stale")
    obj = json.loads(path.read_text())
    normalize("claude", obj)
    return obj


def capture_claude(path, payload):
    # Statusline input also includes transcripts and identifiers. Discard them.
    gauges = {}
    for key, suffix in (("five_hour", "5h"), ("seven_day", "7d")):
        window = (payload.get("rate_limits") or {}).get(key)
        if window and window.get("used_percentage") is not None:
            gauges[suffix] = value(window["used_percentage"], window.get("resets_at"))
    if not gauges:
        return  # An empty startup event must not replace a valid cache.
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".quota-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump({"gauges": gauges}, handle)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main(argv):
    try:
        if argv == ["codex"]:
            obj = codex_native()
        elif len(argv) == 2 and argv[0] == "claude-cache":
            obj = cache_read(argv[1])
        elif len(argv) == 2 and argv[0] == "capture-claude":
            capture_claude(argv[1], json.load(sys.stdin))
            return 0
        else:
            print("usage: quota_readers.py codex | claude-cache PATH | capture-claude PATH", file=sys.stderr)
            return 2
        print(json.dumps(obj))
        return 0
    except (OSError, ValueError, TypeError, KeyError):
        print("quota reader unavailable; check provider usage view manually", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
