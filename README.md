# quota-management

Keep long agent runs within their usage windows. The skill helps a coordinator
place work on authorized accounts with headroom, checkpoint before exhaustion,
and resume after resets. A plain Python watcher writes state and alert files
without asking a model to measure its own remaining quota.

Works with stock Claude Code and Codex. If available, it uses Night, the author's
[public shell toolkit](https://github.com/NightMachinery/.shells), installed at
`~/scripts`, for its usage helpers. Readers are replaceable JSON commands, so
other providers can use the same watcher.

## Install

```sh
npx skills add NightMachinery/quota-management --global \
  --agent claude-code codex --skill quota-management
```

For another Claude profile, set `CLAUDE_CONFIG_DIR` to its configuration directory
and install with `--agent claude-code`.

Start with [the guide](docs/quota-management.md). The skill selects exactly one
setup reference for the current machine. Python 3 and Bash are needed for the
watcher; tmux is optional. MIT licensed, see [LICENSE](LICENSE).
