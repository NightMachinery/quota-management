# Night setup

Night is the author's [public shell toolkit](https://github.com/NightMachinery/.shells),
installed at `~/scripts`. It provides `claude-code-usage-all`, `codex-status` and
the Python readers used here. Use this file when the tooling check finds those
helpers or readers. Without them, select the generic reference instead. This
file contains the complete Night setup; policy and thresholds remain in `SKILL.md`.

## Read usage

If available, use the shell functions in the initialized zsh environment:

```sh
claude-code-usage-all
codex-status
```

These are optional local conveniences, not stock Claude Code or Codex commands.
The first reports the configured Claude accounts and short, weekly and scoped
windows. The second reads configured Codex accounts. Inspect actual window
lengths rather than assuming primary always means five hours. `agy-status`
can report additional provider buckets when installed.

The existing Python readers are `~/scripts/python/claude_code_usage.py --all
--json` and `~/scripts/python/codex_status.py --json`. The watcher detects these
files without reading usage, and prefers the shell helpers at measurement time
with a `zsh -ic` `whence` check. Shell initialization must be trusted and able to
run unattended. Where only functions exist and no reader file or executable is
visible, set `QUOTA_WATCH_TOOLING=night` after the skill's current-shell check.

The Claude reader uses the undocumented
`https://api.anthropic.com/api/oauth/usage` endpoint through the existing local
reader. This dependency is intentional, not a public API guarantee. Client,
authentication and response changes can break it. Keep it replaceable and check
its results against Claude Code `/usage` after changes. The watcher does not
read credentials itself or save raw reader output. The installed reader owns
authentication and any network requests.

## Expired login

An expired login is not zero usage. Check the relevant account in its normal
Claude Code session and use `/usage`; reauthenticate there if needed. Avoid
opening a login session on every polling cycle. The watcher passes
`--no-relogin` to the Claude reader so failures are reported rather than hidden
behind repeated refresh sessions.

If your local setup needs a launcher refresh, use its configured launcher for
that account, from scratch space. For example, with `tmuxnewsh2` available:

```sh
cd ~/tmp
# Replace YOUR_ACCOUNT_LAUNCHER with the authorized account's local launcher.
tmuxnewsh2 quota-login-refresh YOUR_ACCOUNT_LAUNCHER
```

Wait for login readiness or finish the login interactively, then run
`claude-code-usage-all` again. The former fixed-delay refresh sequence was
launcher-specific; readiness and a successful reread are the actual condition.
Use `ec-sep-h` and `ec` only if you want local formatting, not for JSON reads.
Never infer the target account from a hardcoded alias.

## Watcher selection and outputs

Set a private run directory and run a single cycle from the installed skill:

```sh
export QUOTA_WATCH_BASE=~/.local/state/quota-management/run
bash scripts/quota-watch.sh --once
```

The log says `readers: night` and lists the selected sources. The watcher:

- Uses `claude-code-usage-all --json --no-relogin` and `codex-status --json` when
  present, otherwise invokes the existing Python scripts. Shell helpers retain
  the local configured account list. No account aliases are hardcoded here.
- Reads Antigravity only through
  `agy_status_method=statusline agy_status_cache_only_p=y agy-status --json`
  when its helper is present. A cold cache stays unavailable; there is no live
  model turn, login refresh or HTTP fallback in this adapter.
- Maps every returned account/model bucket, validates percentages and reset
  times, ignores identity and unrelated provider fields, and retains stale
  readings with `~`. Deliberately signed-out optional accounts are quiet.
- Uses `windowDurationMins` for Codex window labels. Missing durations retain
  primary/secondary labels. Codex account aliases are ordinal; keep auth-file
  ordering stable or use your own normalized reader for persistent aliases.
- Fills providers lacking Night readers with configured JSON readers or the
  shipped stock adapters. Night readers win duplicate source names.

The stock Codex adapter uses `codex app-server` with `initialize`, `initialized`
and `account/rateLimits/read`, without starting a model turn. It requires an
existing compatible login. See [Codex app-server documentation](https://developers.openai.com/codex/app-server).
If Claude has no Night reader, use its `/usage` view manually, or supply a JSON
reader for its documented status-line quota fields. See
[Claude status-line fields](https://code.claude.com/docs/en/statusline).

To add another reader without loading another reference, set
`QUOTA_WATCH_READERS` to a JSON object mapping source names to command argv
arrays. Each command exits zero and emits an object with required `gauges`:
keys are stable account/window aliases, values have finite numeric `pct` used
and `reset` (Unix seconds or null). Optional `unavailable` and `signed_out` are
lists of account scopes. Unknown data is unavailable, never zero. Commands run
without shell expansion; readers own cache freshness. For exact control,
`QUOTA_WATCH_AUTO=0` uses only these configured commands. Errors never persist
raw command stderr or provider payloads.

The watcher writes `logs/quota-watch.log`, restart baselines, failure counters,
`STATE.md` and alerts under the base directory. Exit codes: `0` means measured,
`1` means a reader failed, `2` means no reader or invalid configuration. The shell
loop stops on `2`, retries `1`, and defaults to a 900-second interval. Set
`QUOTA_WATCH_INTERVAL` to change it. Run one watcher per directory.

## Hourly supervision command

For the shared Claude Code hourly prompt in `SKILL.md`, run:

```sh
zsh -ic 'claude-code-usage-all; codex-status'
```

If a helper is unavailable, use its Python-reader fallback described above;
measure only providers used by running work. Compare readings and reset times
with the saved `## Quota` block and pending alerts. A missing measurement is an
error to investigate, not evidence that an account has headroom.

## Background lifecycle

If available, use the local job helper from the installed skill directory:

```sh
tmux-job-start quota-watch QUOTA_WATCH_BASE="$QUOTA_WATCH_BASE" bash scripts/quota-watch.sh
```

Pass any reader overrides as leading environment assignments too. Otherwise,
use ordinary tmux with an explicit installed path:

```sh
tmux new-session -d -s quota-watch 'bash /path/to/skill/scripts/quota-watch.sh'
```

Verify the new session's environment in its first log. A pre-existing tmux
server can have stale environment variables. Stop the watcher when the run has
no workers, remote jobs or event waiters left:

```sh
# Use the matching stop command for the way you started it.
tmux-job-stop quota-watch
# Or: tmux kill-session -t quota-watch
```

A task runner can wait for `alerts/quota-*.md` with a plain filesystem waiter,
then notify the coordinator once. Do not schedule model turns just to read usage.
After acting on alerts, archive them before re-arming. Record the process/session
and stopped/running status in private state. Restart before the next long task.
