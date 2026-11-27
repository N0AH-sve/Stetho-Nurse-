# Coverage of conditions by actions

Which detected condition has a repair, and what that repair can be expected to do. Thresholds
are not repeated here. Command details are in [commands](commands.md).

"Expected" comes from reading the code: **no repair was tried.**

The catalogue holds five actions. A command listed as a candidate is **not** part of it: adding
one is a separate decision.

## Conditions with a planned action

| Condition | Planned action | What to expect | Do not run when |
| --- | --- | --- | --- |
| Gateway gives no sign of life | `RESTART_CONTAINER` | Restarts the container; a gateway starts only if its intent is `running` | The gateway was stopped on purpose; the state is only unreadable |
| Gateway `stopped` | `RESTART_GATEWAY` | **Nothing** if the s6 service is down, yet exit code 0 | `desired_state` is `stopped` |
| Gateway `startup_failed` | `RESTART_GATEWAY` | Nothing if the service is down; the cause may remain | Configuration error |
| Gateway `degraded` | `RESTART_GATEWAY` | May restart a running gateway. After a watchdog stop, depends on the service state | Platforms are only waiting for their own retry |
| Platform not connected | `RESTART_GATEWAY` | May reconnect; restarts **all** platforms | `disabled` or `paused`; a `fatal` credential or setting error |
| Scheduler silent or failing | `RESTART_GATEWAY` | May help a stuck thread. Not a permission, disk or file error | `cron/ticker_last_error` shows a persistent cause |
| Cron job in error | `RERUN_JOB` | Runs the whole job now; may fail again | The job is paused (not run), running, or has effects that must not repeat |
| Cron job failing repeatedly | `PAUSE_JOB` | Stops later runs; repairs nothing | |
| Cron result not delivered | `RERUN_JOB` | **Runs the whole job again**, not only the delivery | The platform is still down; effects must not repeat |
| Cron job late | `RERUN_JOB` | Runs it now | The scheduler is down: fix it first; the job is paused |
| Kanban task silent or too long | `UNBLOCK_KANBAN_TASK` | Stops the worker and queues the task again, with fresh retries | The task fails in a loop; the worker may still be working |

## Consequences for the rules

- **Intent.** Read `desired_state` before any gateway repair. Otherwise an automatic action can
  work against a deliberate stop.
- **Stopped gateway.** The planned action may do nothing. `gateway start` is the candidate to
  study.
- **Platform states.** "Not connected" includes `disabled` and `paused`, which are not faults.
- **Verification.** Success cannot rest on the exit code, nor on the freshness of
  `gateway_state.json` alone. See [commands](commands.md#the-cli-makes-the-state-file-look-alive).
- **Duration.** `RERUN_JOB` lasts as long as the job. A short timeout would report a failure
  while the job may still run.
- **Boards and profiles.** A target outside the active board or the current profile needs it to
  be selected.
- **Name.** `UNBLOCK_KANBAN_TASK` reclaims a running task. It does not unblock a blocked one.

## Conditions without an action

| Condition | Manual step or candidate |
| --- | --- |
| Gateway state unreadable, or slow to answer | Check files and access; wait for the next reading |
| Platform asking for attention | Read the error; fix credentials or settings |
| Cron job running for too long | No command found to stop a run |
| Cron job paused | Candidate: `cron resume` |
| Kanban task blocked | A human decides. Candidate: `kanban unblock` |
| Kanban task failing repeatedly | Read the last error |
| Session warnings | None: observation only |
| Known weaknesses: disk, memory, sizes, file owner, network disk, restart loop, delivery backlog | Manual steps only. `sessions prune` deletes data and is expected to be refused while the gateway runs |
| Anomalies, including failed actions | Reported, not repaired |

## States found in the code with no rule yet

| State | Question to settle |
| --- | --- |
| Gateway `draining` | Normal for a while: from when is it a fault? |
| Cron `blocked_config` | A setting must be fixed; a rerun would not help |
| Cron `delivery_queued` | Probably normal: from when is it a fault? |
| Cron job `state: error` | The next run cannot be computed |
| Incident `resolved` | Close the incident in STETHO |

## Candidates

| Command | Would cover | Main risk |
| --- | --- | --- |
| `gateway start` | Stopped gateway | Overrides a deliberate stop |
| `gateway stop` | Restart loop | Takes Hermes offline |
| `cron resume` | Paused job | May start a missed run |
| `kanban unblock` | Blocked task | Overrides a block meant for a human; resets retries |
| `sessions prune` | Large session database | Permanent data loss |

None is recommended for automatic use.
