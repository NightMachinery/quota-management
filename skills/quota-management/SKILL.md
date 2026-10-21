---
name: quota-management
description: Manage usage headroom across authorized LLM accounts and providers during long agent runs. Monitor quota without model turns, checkpoint before exhaustion, hand off work, and rebalance after resets.
---

# Quota management

First choose one setup reference with a cheap check in the current shell:

```sh
if command -v claude-code-usage-all >/dev/null 2>&1 \
  || command -v codex-status >/dev/null 2>&1 \
  || test -f ~/scripts/python/claude_code_usage.py \
  || test -f ~/scripts/python/codex_status.py; then
  echo night
else
  echo generic
fi
```

If available, use [Night setup](references/night-setup.md). Otherwise read
[generic setup](references/generic-setup.md). Read only the selected file.

## Account and model policy

Track the short usage window and the weekly window separately, along with any
model-specific pool. Missing data means unknown headroom. Plans and providers
differ; never assume a second provider has a smaller allowance, a primary slot
means five hours, or all accounts expose every window.

Check which account each worker actually uses. In-process children commonly
inherit the parent account, so moving the coordinator may be necessary to move
future spend. Independently configured workers can use another authorized
account without moving the coordinator. Record the real mapping privately.

Headroom does not authorize a new account, provider, model, or data recipient.
Use an installed delegation skill for model selection when available; otherwise
follow existing user authorization. Account choice and capability are separate
decisions. Use a capable economical model for mechanical work and reserve
scarce model pools for work that benefits from them.

## Cadence and thresholds

Measure at task start and before a burst of workers. During long work, prefer
the plain watcher for frequent measurements. One hourly coordinator check can
catch reader problems and make placement decisions. Stop monitoring when workers,
remote jobs and waiters finish; restart before the next long run.

The following are **policy defaults, not measurements from a real account**.
Adapt them to task size, reset time, observed consumption and the user's budget:

- At 70% used, freeze expensive launches and prepare a coordinator handoff.
- At 85%, stop launching on that account and checkpoint active workers.
- At 95%, finish writing durable state before another expensive step.
- At a reset, verify all relevant windows before moving heavy work back.

The watcher alerts on upward crossings of 70, 85, 90, 95 and 100 percent. It
reports only the highest crossing in one cycle. A newly seen gauge alerts only
at 85 or above. A large drop or later reset time is a reset candidate, not proof
that every pool has replenished. An unused window's moving reset time is quiet.

## Hourly Claude Code supervision

When Claude Code exposes `CronCreate`, start one hourly supervision job alongside
the watcher before a long task's first worker launch. Use cron `7 * * * *`, an
off-minute, and this prompt verbatim. The measurement command lives in the one
setup reference already selected above:

> HOURLY QUOTA SUPERVISION. Frequent measurements belong to the plain watcher.
> Save the current `## Quota` block, then run the hourly supervision command from
> the already-selected setup reference. Compare its usage and reset times with
> that saved block and `alerts/`. Act only if an account used by running workers
> is at or above 85% used with more than 30 minutes until reset (checkpoint its
> workers and hand off to an authorized account with headroom), or a reset has
> happened (verify the relevant windows, then rebalance heavy launches there).
> Otherwise say "quota: nothing to rebalance" and continue pending work. Missing
> or stale data remains unknown; report a measurement failure instead of claiming
> nothing needs rebalancing. If no workers, remote jobs or event waiters remain,
> stop the watcher, use CronList to find this job, then CronDelete it. Keep the
> check under 3 tool calls by combining independent reads and necessary actions.

Record the job identity and watcher session in private state. On idle, use
`CronList` then `CronDelete` for this job and stop the watcher with the selected
setup's stop command. Re-create both before the next long task. Where scheduling
tools are unavailable, perform the hourly check manually while work is running.
Do not replace the watcher's frequent measurements with frequent model turns.

## Decisions during a long run

1. Place new work where both the short and long windows have room. Keep enough
   headroom to review results, write state and arrange a handoff.
2. Script mechanical measurements, log collection and file transformations.
   Monitoring with a model spends the very resource being monitored.
3. Budget long interactive workers separately. A single iterative writing or
   design task can dominate an account's usage. Check it before extending it.
4. Use a written brief and named result file for a second-provider consultation,
   so its answer can be reused. Avoid repeating the same review across sessions.
5. Near exhaustion: stop launches, checkpoint running work, write state, then
   hand off. Tell children to save results on disk because their original parent
   may be the only recipient of in-process completion messages.
6. If a reset is close and safe work can wait, holding may cost less than a
   coordinator handoff. Use a plain timer or event waiter. If all authorized
   accounts are exhausted, checkpoint and pause with a precise resume point.
7. After a reset, resume from the checkpoint and list what remains. Do not
   restart a rate-limited child from the beginning when its work is on disk.

These rules preserve qualitative lessons from sustained runs: child workloads
can dominate coordinator usage, interactive revision can consume a whole window,
and even one external review can materially change the remaining budget.
No fixed number of reviews or children is universally affordable.

## Durable state and alerts

Keep usage measurements, account mappings and results outside public repositories.
Use the shared default `~/.agents/runs/<project>/<run>/`, outside every repository.
The watcher maintains generated `Gauge` and `Reader`
lines inside `## Quota` while preserving coordinator notes and other sections.
A `~` after a percentage means last known data, not a fresh measurement.

```text
## Quota (checked TIMESTAMP)
Gauge provider.account.5h: NN% used (resets TIMESTAMP)
Gauge provider.account.7d: NN%~ used (resets TIMESTAMP)
Plan: next action and authorized destination
Next rebalance: verify headroom after the named reset
Watcher: running, with process/session name, or stopped because idle
History: previous check time and trend
```

Check the wall clock. Reset times can drift slightly; avoid treating small
changes as an event. Watch for error alerts as well as threshold alerts.
Read each alert, act, then archive or remove it so the waiter can re-arm.
The watcher never deletes alerts or switches accounts itself.
