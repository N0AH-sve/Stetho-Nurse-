# Platforms

> Status: implemented.

## Purpose

Shows the connection state of Discord and the other platforms.

## Sources and format

The `platforms` field of [`gateway_state.json`](../hermes/gateway.md#platforms), for every
expected [profile](gateway.md#profiles). `stetho/modules/platforms/reader.py` reads it, with the
adapter `adapters/hermes-0.21.yaml`. The error text and code of a platform are never kept: the
page only says that an error is not shown and points to the Hermes logs. Platform and profile
names of the file are accepted only if they are short and simple.

## When platforms are assessed

A file written by a gateway that does not run may say anything. The platforms of a gateway are
[not assessed](probe.md#result-format) when it is `stopped` or `startup_failed`, stopped by a
watchdog, or has no real sign of life that is recent enough: no usable date, a date written by a
command, or older than the [late threshold of the gateway](gateway.md#thresholds), 2 minutes by
default. They are then `unknown`, without advice, action or detail.

A state file that cannot be used is left to the `gateway` module, which reports why. If that
module is disabled, this one reports it: `unknown`, with the anomaly.

## Rules

[`rules.yaml`](../../stetho/modules/platforms/rules.yaml) holds the threshold, the meaning of
each state and the repair.

| State | Status | Action offered |
| --- | --- | --- |
| `connected` | `ok` | None |
| `disabled`, `paused` | `info`: not used on purpose | None |
| `fatal` | `critical` | None: a restart does not fix a credential or setting |
| `connecting`, `retrying`, `disconnected`, for 5 minutes or less | `warn` | None |
| The same, for more than 5 minutes | `critical` | `RESTART_GATEWAY` |
| Unknown state | `warn` | None |

- **Attention.** With `needs_attention`, a platform is at least `warn`, even if `connected`,
  `paused` or `disabled`.
- **Delay.** It starts at `retrying_since` when Hermes gives a plausible date (between 2020 and
  5 minutes ahead). Otherwise, at the first reading where STETHO sees the platform waiting; this
  delay starts again when STETHO restarts.
- **Actions** are offered for the main gateway only, since an action names no profile, and never
  when its `desired_state` is `stopped` or unknown. A restart of the gateway restarts every
  platform. This includes the platforms of the profiles that the main gateway serves.
- **Threshold.** `modules.platforms.overrides.critical_after_minutes` in `stetho.yaml`.

### Former entries

Hermes merges the entries over time. An entry that was not written by the process that wrote
the file (`writer_pid` and `writer_start_time` against `pid` and `start_time`) is `info`,
"former state, not checked", so that a removed platform does not keep the page orange.

**Accepted limit:** a real platform that has not written its state again is ignored for a while
if another one already has.

## Card

One item for each platform, named after it, with the profile when there are several gateways or
when one gateway serves several profiles.

| Case | Card |
| --- | --- |
| A platform has a problem | The worst status; the sentence names the platform |
| All connected | `ok`, with their number; former and unused ones are not counted |
| No platform, or only `info` ones | `info`: it does not count in the overall status |
| Entries, none from the running gateway | `unknown` |
| `platforms` absent or not as expected, or an unusable entry or name | `unknown`; the other entries are read |
| Profiles that cannot be listed | `unknown` |
| Some gateway does not run normally | At least `unknown`; not assessed when nothing else explains it |

## Anomalies

| Problem | Anomaly |
| --- | --- |
| `platforms` or an entry not as expected, unknown field, wrong type | `schema_changed` |
| Unknown state | `unexpected_value` |
| Impossible `retrying_since` | `implausible_value` |

Platforms that are not assessed are not parsed and raise no anomaly. Earlier anomalies stay
while the whole card is not assessed. **Limit:** when only some gateways are down, their earlier
anomalies end, although they were not checked again.

## Tests

`tests/stetho/test_platforms.py` covers every rule, the former entries, the gateways that do not
run, the anomalies and the profiles, from made-up files.

## Decisions

- **Not assessed** when the gateway does not run, so that an old "connected" cannot look healthy.
- **Former entries** are shown and not judged.
- **Nothing to monitor** gives an `info` card.
- **Fatal** has no repair; the initial specification offered one for any platform that was not
  connected for 5 minutes.
