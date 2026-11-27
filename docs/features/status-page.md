# Status page

> Status: to be written.

## Purpose

Gives a non-developer the state of Hermes and what to do about it.

## Planned content

- Overall banner
- Cards, tabs and action log
- Confirmations and refresh

## Decisions

- **No problem.** The banner shows a fixed healthy state.
- **Nothing checked.** With no result, or only `info` ones, the overall status is `unknown` and
  the banner says that nothing could be checked. See [statuses](probe.md#statuses).
- **Problems.** They are listed from the most to the least severe: `critical`, `warn`, then
  `unknown`. The banner shows the most severe one; `unknown` is orange and `info` does not count.
- **Sorting.** The user can sort problems, for example by problem, source or solution.
- **Languages.** French, English and Spanish, chosen at installation. English is the default.
