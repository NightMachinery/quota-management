#!/usr/bin/env python3
"""Quota watcher. Require QUOTA_WATCH_BASE; see docs/quota-management.md.

--once runs one cycle. --detect prints night or generic without reading usage.
Reader commands use the JSON contract in references/generic-setup.md.
"""
from __future__ import annotations
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from quota_readers import choose_readers, measure, tooling

BASE = Path(os.environ.get("QUOTA_WATCH_BASE", "." )).expanduser()
LOGDIR = BASE / "logs"
ALERTDIR = BASE / "alerts"
STATE_MD = BASE / "STATE.md"
LOGFILE = LOGDIR / "quota-watch.log"
STATEFILE = LOGDIR / "quota-watch.state"
FAILFILE = LOGDIR / "quota-watch.fail"
THRESHOLDS = [70, 85, 90, 95, 100]  # Policy defaults, not measured usage.
STATE_VERSION = "#quota-watch-state v2"

def gauge_names(gauges):
    return sorted(gauges)

def now_iso() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def log(line: str, *, echo: bool = True) -> None:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    with open(LOGFILE, "a", encoding="utf-8") as fh:
        fh.write(line.rstrip("\n") + "\n")
    if echo:
        print(line.rstrip("\n"), flush=True)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fmt_hm(epoch) -> str:
    if not epoch:
        return "??:??"
    return datetime.fromtimestamp(float(epoch)).astimezone().strftime("%H:%M")


def fmt_stamp(epoch) -> str:
    if not epoch:
        return "-"
    return datetime.fromtimestamp(float(epoch)).astimezone().strftime("%Y-%m-%d %H:%M")


def read_state() -> dict:
    """gauge -> {pct, reset}. Empty dict means 'first run'."""
    try:
        lines = STATEFILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    if not lines or not lines[0].startswith(STATE_VERSION):
        return {}  # older/unknown format: treat as a first run
    out = {}
    for line in lines[1:]:
        parts = line.split("|")
        if len(parts) < 3 or not parts[0]:
            continue
        try:
            out[parts[0]] = {
                "pct": int(parts[1]),
                "reset": float(parts[2]) if parts[2] not in ("", "-") else None,
            }
        except ValueError:
            continue
    return out


def write_state(gauges: dict) -> None:
    rows = [STATE_VERSION, "# gauge|pct|reset_epoch|reset_local|written=%s" % now_iso()]
    for g in gauge_names(gauges):
        v = gauges.get(g)
        if not v:
            continue
        rows.append("%s|%d|%s|%s" % (
            g, v["pct"], ("%.0f" % v["reset"]) if v["reset"] else "-", fmt_stamp(v["reset"])))
    atomic_write(STATEFILE, "\n".join(rows) + "\n")


def read_fails() -> dict:
    try:
        return json.loads(FAILFILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_fails(d: dict) -> None:
    atomic_write(FAILFILE, json.dumps(d, indent=2) + "\n")


# ----------------------------------------------------------------------- alerts

def action_for(gauge: str, kind: str, pct, minutes) -> str:
    if kind == "error":
        return "check the usage command manually"
    if kind == "reset":
        return "profile has headroom again; rebalance heavy spawns here"
    if gauge.startswith("agy."):
        if pct is not None and pct >= 85 and (minutes is None or minutes > 30):
            return "stop spawning on this account/model bucket; checkpoint and hand off"
        return "watch this account/model bucket; prefer an account with headroom"
    if gauge.endswith(".5h"):
        if pct is not None and pct >= 85 and (minutes is None or minutes > 30):
            return "stop spawning on this profile; tell its children to checkpoint and hand off"
        return "watch this profile; prefer the other one for the next burst of spawns"
    return "long windows recover slowly; shift heavy work to an authorized account with headroom"


def write_alert(gauge: str, kind: str, old, new, reset_epoch, *, detail: str = "") -> Path:
    ALERTDIR.mkdir(parents=True, exist_ok=True)
    minutes = None
    if reset_epoch:
        minutes = int((float(reset_epoch) - time.time()) / 60)
    path = ALERTDIR / ("quota-%d-%s-%s.md" % (int(time.time()), gauge, kind))
    action = action_for(gauge, kind, new if isinstance(new, int) else None, minutes)
    body = [
        "# quota alert: %s %s" % (gauge, kind),
        "",
        "- time: %s" % now_iso(),
        "- gauge: %s" % gauge,
        "- percent: %s -> %s" % ("-" if old is None else old, "-" if new is None else new),
        "- reset: %s%s" % (fmt_stamp(reset_epoch),
                           (" (in %d min)" % minutes) if minutes and minutes > 0 else ""),
        "- kind: %s" % kind,
        "- action: %s" % action,
    ]
    if detail:
        body += ["- detail: %s" % detail]
    body += ["", "Written by quota-watch.sh. Delete this file once you have acted on it.", ""]
    # Never overwrite an existing alert (same second, same gauge, same kind).
    n = 0
    while path.exists():
        n += 1
        path = ALERTDIR / ("quota-%d-%s-%s.%d.md" % (int(time.time()), gauge, kind, n))
    path.write_text("\n".join(body), encoding="utf-8")
    log("%s  ALERT %s %s %s -> %s reset=%s :: %s" % (
        now_iso(), gauge, kind, "-" if old is None else old,
        "-" if new is None else new, fmt_stamp(reset_epoch), action))
    print("ALERT %s %s %s -> %s :: %s (%s)" % (
        gauge, kind, "-" if old is None else old, "-" if new is None else new, action, path),
        flush=True)
    return path


def diff_alerts(old_state: dict, new_gauges: dict) -> int:
    fired = 0
    first_run = not old_state
    for gauge in gauge_names(new_gauges):
        cur = new_gauges.get(gauge)
        if not cur:
            continue
        new_pct, reset = cur["pct"], cur["reset"]
        prev = old_state.get(gauge)

        if prev is None:
            # First sighting: stay quiet unless it is already serious.
            hit = max([t for t in THRESHOLDS if new_pct >= t], default=None)
            if hit is not None and hit >= 85:
                write_alert(gauge, "threshold-%d" % hit, None, new_pct, reset,
                            detail="first run" if first_run else "gauge seen for the first time")
                fired += 1
            continue

        old_pct, old_reset = prev["pct"], prev.get("reset")

        # A reset: the percentage fell a lot, or the reset time moved later.
        # A rolling window that sits at 0 keeps pushing its reset time later
        # without anything having reset; only count "moved later" when the
        # window had been used (old_pct > 0).
        moved_later = bool(reset and old_reset and reset > old_reset + 60 and old_pct > 0)
        if (old_pct - new_pct) > 20 or moved_later:
            write_alert(gauge, "reset", old_pct, new_pct, reset,
                        detail=("reset time moved later" if moved_later else "percent dropped"))
            fired += 1
            continue

        hit = max([t for t in THRESHOLDS if old_pct < t <= new_pct], default=None)
        if hit is not None:
            write_alert(gauge, "threshold-%d" % hit, old_pct, new_pct, reset)
            fired += 1
    return fired


def source_failure(source: str, err: str, fails: dict) -> None:
    n = int(fails.get(source, 0)) + 1
    fails[source] = n
    log("%s  MEASURE-FAILED %s (consecutive=%d): %s" % (now_iso(), source, n, err))
    if n == 2 or (n > 2 and n % 8 == 0):
        write_alert(source, "error", n - 1, n, None, detail=err[:500])


def update_state_md(gauges, errors=None, signed_out=None, *, no_reader=False):
    block = ["## Quota (checked %s by quota-watch.sh)" % now_iso()]
    if no_reader:
        block.append("Monitor: no reader configured; use the provider usage view manually.")
    for gauge in gauge_names(gauges):
        value = gauges[gauge]
        block.append("Gauge %s: %s%%%s used (resets %s)%s" % (
            gauge, value["pct"], "~" if value.get("stale") else "",
            fmt_stamp(value.get("reset")), " EXHAUSTED" if value["pct"] >= 100 else ""))
    for source in sorted(errors or {}):
        block.append("Reader %s: unavailable (check reader manually)" % source)
    for source in sorted(signed_out or set()):
        block.append("Reader %s: not signed in" % source)
    try:
        lines = STATE_MD.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = ["# STATE", ""]
    start = next((i for i, line in enumerate(lines) if line.startswith("## Quota")), None)
    generated = ("Gauge ", "Reader ", "Monitor:")
    if start is None:
        insertion = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
        lines = lines[:insertion] + block + [""] + lines[insertion:]
    else:
        end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
        carry = [line for line in lines[start + 1:end] if line.strip() and not line.startswith(generated)]
        lines = lines[:start] + block + carry + [""] + lines[end:]
    atomic_write(STATE_MD, "\n".join(lines) + "\n")


def cycle():
    LOGDIR.mkdir(parents=True, exist_ok=True)
    ALERTDIR.mkdir(parents=True, exist_ok=True)
    try:
        mode, readers = choose_readers()
    except ValueError:
        log("reader configuration invalid; check QUOTA_WATCH_READERS")
        return 2
    log("%s  readers: %s (%s)" % (now_iso(), mode, ", ".join(readers) or "none"))
    old = read_state()
    if not readers:
        update_state_md({g: {**v, "stale": True} for g, v in old.items()}, no_reader=True)
        log("no reader configured; monitoring unavailable; use manual usage views")
        return 2
    fresh, errors, signed_out = {}, {}, set()
    fails = read_fails()
    for source, reader in readers.items():
        gauges, problems, inactive = measure(source, reader)
        fresh.update(gauges)
        errors.update(problems)
        signed_out.update(inactive)
        if not gauges and not problems and not inactive:
            errors[source] = "no gauges returned"
    active_sources = set(readers)
    # Retain baselines only for readers still configured. Never compare stale
    # readings or signed-out accounts against fresh values to infer a reset.
    old = {g: v for g, v in old.items()
           if g.split(".", 1)[0] in active_sources
           and not any(g == s or g.startswith(s + ".") for s in signed_out)}
    for source in set(fails) | set(errors):
        if source in errors:
            source_failure(source, errors[source], fails)
        else:
            fails[source] = 0
    write_fails(fails)
    diff_alerts(old, fresh)
    merged = {**old, **fresh}
    write_state(merged)
    visible = {g: {**v, "stale": g not in fresh} for g, v in merged.items()}
    update_state_md(visible, errors, signed_out)
    log("%s  %s" % (now_iso(), " ".join("%s=%s%%%s" % (
        g, v["pct"], "~" if v.get("stale") else "") for g, v in visible.items()) or "no gauges"))
    return 1 if errors else 0


def main(argv):
    if argv == ["--detect"]:
        print(tooling())
        return 0
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv not in ([], ["--once"]):
        print("unknown argument", file=sys.stderr)
        return 2
    if not os.environ.get("QUOTA_WATCH_BASE"):
        print("set QUOTA_WATCH_BASE to a private run directory", file=sys.stderr)
        return 2
    return cycle()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
