# Quota management guide

The skill separates placement decisions from measurement. Account headroom
influences where authorized work runs; the watcher measures and alerts without
making that decision or launching workers.

## Start here

Load [the skill](../skills/quota-management/SKILL.md) and run its tooling check.
Read exactly one reference:

- [Night setup](../skills/quota-management/references/night-setup.md) for machines
  with the existing shell helpers or Python readers.
- [Generic setup](../skills/quota-management/references/generic-setup.md) for
  stock Claude Code, Codex and configurable JSON reader commands.

Both explain how to run a single cycle, start a background watcher, and stop it.
The shared skill also supplies the hourly Claude Code supervision prompt and
its create/delete lifecycle; the selected setup reference supplies the command.
A machine with only one provider does not need to install the other.

## What the watcher owns

`QUOTA_WATCH_BASE` is required and identifies a private run directory. It owns
`logs/quota-watch.log`, `logs/quota-watch.state`, `logs/quota-watch.fail` and
`alerts/quota-*.md`, and updates generated lines in `STATE.md`. Run one watcher
per directory to avoid competing writes. Other agents can edit coordinator
notes, but must not race a watcher update of `STATE.md`.

- Cross-cycle baselines and consecutive failures survive restarts.
- A failed reader retains remembered gauges marked `~`, including total outages.
- Reader failures alert on the second consecutive failure and every eighth
  consecutive failure afterwards. Successful sources continue normally.
- Signed-out optional accounts clear old gauges and failure counts without an
  error alert. A signed-out account is not an available account.
- Alert names escape unsafe gauge characters and avoid overwriting existing files.
- Raw provider responses and subprocess stderr are discarded. Runtime usage
  values and configured aliases are still private; do not commit runtime files.

The alert defaults and synthetic test values are policy and fixtures, not
real-run usage, costs or billing. A later reset time or a usage drop greater
than twenty points is a reset candidate. Read the provider view before treating
it as replenished headroom. Other windows can remain exhausted.

A single-cycle exit code of `0` means measurement succeeded, `1` means one or
more readers were unavailable, and `2` means missing or invalid configuration.
With no reader, the watcher writes an explicit unavailable status, marks any
old data stale, and exits `2`. The shell loop stops on `2`, retries `1`, and
uses `QUOTA_WATCH_INTERVAL` seconds between cycles (default 900).

## Validate changes

From the repository root:

```sh
python3 -m unittest discover -s skills/quota-management/scripts -p 'test_*.py' -v
```

Tests use synthetic fixtures, temporary directories and stub commands. They
exercise reader selection, the JSON contract, stock adapters, status-line cache
freshness, partial failures, reset/threshold alerts and shell `--once`. No user
authentication, provider requests or model turns are needed for the tests.
