# Generic setup

Use this file on machines without Night helpers. The policy is in `SKILL.md`;
this file covers measurement and the watcher. No zsh, custom profile launcher or
separate usage-reader package is required.

## Read usage manually

- Claude Code: open `claude` with the intended login and enter `/usage`. Read the
  session and weekly bars, resets and any model-specific limits it shows.
  [Claude Code commands](https://code.claude.com/docs/en/commands) documents the
  command. The [plan guidance](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)
  explains session and weekly limits. `/cost` measures session consumption and
  is not a substitute for remaining subscription allowance.
- Codex: open `codex` with the intended login and enter `/status`. Inspect its
  limits and reset times. [Codex commands](https://developers.openai.com/codex/cli/slash-commands)
  documents the interactive command surface. Missing windows remain unknown;
  API authentication and some plans do not expose subscription windows.

Repeat under each authorized account's own configuration. Two configurations
signed into the same account are not independent budgets. Record aliases and
reset times privately. A command that reports login status is not a usage read.

## Built-in reader adapters

The watcher auto-detects the `codex` executable and runs `quota_readers.py codex`.
That adapter starts `codex app-server` over its default stdio transport, performs
`initialize` and `initialized`, then requests `account/rateLimits/read`. It
starts no thread or model turn. It uses the existing Codex login, requires
provider-backed authentication and discards unrelated response fields.

The [app-server reference](https://developers.openai.com/codex/app-server)
provides the handshake, method and field definitions. Each returned pool becomes
separate gauges. Window lengths come from `windowDurationMins`, with 300 mapped
to `5h` and 10080 to `7d`. `primary` and `secondary` are slot names, not fixed
durations. Null or absent windows are not synthesized. Other durations retain
their reported lengths; an absent duration retains the slot name.

For Claude, the shipped adapter captures the documented status-line input.
[Claude status-line fields](https://code.claude.com/docs/en/statusline) include
`rate_limits.five_hour` and `rate_limits.seven_day`, with `used_percentage` and
`resets_at`. These fields depend on the plan and appear after a model response.
Do not send a model request merely to populate them.

To enable capture, incorporate this command into your Claude `statusLine`
configuration, using the default Claude Code installed location. Adjust the path if your agent
installs skills elsewhere:

```text
python3 ~/.claude/skills/quota-management/scripts/quota_readers.py capture-claude ~/.cache/quota-management/claude.json
```

For the default Claude configuration directory, the corresponding entry in
`~/.claude/settings.json` is:

```json
{"statusLine":{"type":"command","command":"python3 ~/.claude/skills/quota-management/scripts/quota_readers.py capture-claude ~/.cache/quota-management/claude.json"}}
```

Merge this entry with existing settings. For an alternate account, use its own
settings file and a separate cache path.

The status-line command receives JSON on stdin. This capture command prints
nothing and saves only quota fields. If you already have a status-line script,
pass the same buffered stdin to both it and the capture command, then print your
usual status. Do not pipe only the rendered status text into the capture helper.
Use a separate cache per account and never combine unrelated accounts in one
file. No credentials or transcripts are saved.

Configure the watcher to read that cache:

```sh
export QUOTA_WATCH_CLAUDE_CACHE=~/.cache/quota-management/claude.json
```

Absent fields, missing cache files and stale caches are unavailable data. Cache
age is limited by `QUOTA_WATCH_CACHE_MAX_AGE` (default 1800 seconds). It is a
sample from the last status-line update, not a fresh provider query. A startup
event without rate-limit fields does not overwrite a valid sample. When it ages
out, use `/usage` manually or configure another reader. Undocumented endpoints
are also usable through a custom reader; label their dependency and validate
against the provider view after client or schema changes.

## Pluggable reader contract

Set `QUOTA_WATCH_READERS` to a JSON object mapping opaque source names to argv
arrays. Source names use letters, digits, underscores or hyphens. For example:

```sh
export QUOTA_WATCH_READERS='{"provider":["python3","/path/to/my_reader.py"]}'
```

Commands run directly, without shell expansion, under a bounded timeout. Use
an explicit `bash -c` or `zsh -ic` argv only when you intentionally need a shell.
A `~` embedded in JSON is not expanded. Keep commands and local paths in private
configuration, not public documentation or commits.

A reader exits zero and prints exactly one JSON object to stdout:

```json
{"gauges":{"account-a.5h":{"pct":35,"reset":null},"account-a.7d":{"pct":45,"reset":null}},"unavailable":["account-b"],"signed_out":["account-c"]}
```

All numbers and account names in this example are **synthetic**. The contract is:

- `gauges` is required. Each key identifies an account/window or model bucket
  within that source. Use stable aliases; changing an alias creates a new gauge.
- `pct` is finite numeric percent **used**, between zero and one hundred. Convert
  remaining percentages before returning them. `reset` is null or nonnegative
  Unix epoch seconds. Never return booleans, strings or a fake zero for unknowns.
- Optional `unavailable` lists account/scope identifiers that failed. Optional
  `signed_out` lists deliberately unused signed-out scopes, which clear their
  remembered gauges without an error alert. A scope cannot simultaneously have
  fresh gauges and be unavailable or signed out.
- Empty gauges with no status mean a measurement failure. A total failure can
  exit nonzero. Partial success returns valid gauges and unavailable scopes with
  exit zero. Failures of one reader never hide another reader's success.
- Do not include identity, credentials, billing or provider response bodies.
  Only normalized quota values and aliases are saved; unknown fields are ignored.
  The watcher prefixes keys with the source and escapes unsafe path characters.
- The reader is responsible for cache freshness. A successful read must represent
  current enough data for the run; a copied JSON snapshot is not a live reader.

`QUOTA_WATCH_AUTO=0` disables auto-detection and uses only configured commands.
Use it when you want exact control over the providers measured. With no detected
or configured reader, exit code `2` and an explicit status replace silent success.

## Hourly supervision command

For the shared Claude Code hourly prompt in `SKILL.md`, use this command with
the private base directory and reader variables already configured:

```sh
bash ~/.claude/skills/quota-management/scripts/quota-watch.sh --once
```

Adjust the installed path for another agent or profile. This reads the stock
Codex adapter and configured Claude cache or custom readers, updates `## Quota`,
and reports transitions in `alerts/`. Compare with the block saved before the
command. Unavailable or stale Claude data still needs a manual `/usage` read;
never infer headroom from the absence of an alert.

## Run and stop

From the installed skill directory:

```sh
export QUOTA_WATCH_BASE=~/.local/state/quota-management/run
bash scripts/quota-watch.sh --once
```

Inspect `STATE.md` and `logs/quota-watch.log` in that directory. The log says
`readers: generic` and names the chosen source aliases. With only one provider,
configure only that provider. A reader failure returns `1`; fix its setup before
relying on unattended monitoring. Base-directory/configuration errors return `2`.

For background monitoring, use your supervisor or, if tmux is installed:

```sh
tmux new-session -d -s quota-watch 'bash /path/to/skill/scripts/quota-watch.sh'
```

Export the reader variables and base directory before launching. tmux servers
can retain old environments; pass updated variables explicitly with `tmux -e`
or verify the resulting log. Run only one watcher per base directory.

Alerts appear only on transitions and repeated failures. Consume them with the
runtime's event mechanism, or a plain filesystem waiter:

```sh
while ! command find "$QUOTA_WATCH_BASE/alerts" -name 'quota-*.md' -print -quit | command grep -q .; do
  sleep 60
done
```

After reading and acting, archive consumed alerts. Stop when the task is idle:

```sh
tmux kill-session -t quota-watch
```

Record `Watcher: stopped (idle)` in the private state file. Restart before the
next long run. The watcher itself never launches workers or changes accounts.
