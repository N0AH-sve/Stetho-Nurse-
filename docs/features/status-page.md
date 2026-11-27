# Status page

> Status: implemented for the available module, `gateway`. Actions, the action log and the
> Sessions and Known weaknesses tabs come with their steps.

## Purpose

Gives a non-developer the state of Hermes and what to do about it.

## Content

The page is at the root address of STETHO.

| Part | Content |
| --- | --- |
| Banner | Sentence of the overall state, the status in words, and when the last reading was made |
| Problems | One line per problem, from the most to the least severe: status, source, problem, what to do. Shown only when there is a problem |
| Cards | One per sensor: status, title, group, sentence, what to do, and details that fold, with one block per item |
| Anomalies | "Anomalies (n)", folded: module, source, code, sentence, received and expected values, first and last time seen, count. Shown only when there is one |

- **Problems.** Failing items are listed instead of the card that sums them up. A card that is
  more severe than its items is listed too. A column title sorts the list; a second click
  reverses it.
- **Colours.** Banner: green for `ok`, orange for `warn` and `unknown`, red for `critical`. A
  card pill is grey for `unknown` and `info`. A status is always written in words too.
- **Times.** The last reading and the anomalies show how long ago, with the exact date in the
  time zone of the installation on hover. The durations inside sentences are plain text.
- **No button.** Nothing can be changed from the page: actions come with NURSE.
- **Cards.** One generic card serves every module, since all results have the same format.

## Rendering and refresh

The server renders the whole page (`stetho/web/page.py` prepares it, `templates/status.html`
presents it), with automatic escaping. The page does not use the API.

Every 30 seconds, and when the tab becomes visible again, `static/page.js` fetches the page,
reads it with `DOMParser` and swaps its content, without `innerHTML`. What the reader opened,
the sort order and the keyboard focus are kept.

- **Failed refresh.** After an error, a refusal, an unexpected page or 10 seconds without an
  answer, a warning is shown, the banner turns orange, the pills turn grey and the content fades,
  so that old data does not look healthy. A later success removes the warning.
- **Stale report.** The server itself shows it as [out of date](api.md#reading-routes).
- **Without JavaScript**, a message asks to reload the page. **Limit:** such a page, once loaded,
  does not say that it grows old. Likewise, a browser that pauses the timer of a hidden tab
  updates it when the tab is shown again.

## Behind a reverse proxy

All addresses are relative. The proxy is expected to remove its prefix, such as `/stetho/`, and
the address to end with `/`. **To check at deployment.**

## Phone and accessibility

On a narrow screen each problem becomes a block and the cards take one column. Controls work with
the keyboard, with a visible focus.

## Tests

`tests/stetho/test_page.py` covers what the server renders: states, escaping, stale reports,
problems, static files and their protection. It only reads the script.

**The script has no automated test.** It was checked by hand in headless Chrome on made-up data:
sorting, refresh keeping the open parts, sort and focus, the warning after a network error, a 401
or an unexpected page, the return to normal, and a 390 pixel wide screen. Repeat this after a
change of the script.

## Decisions

- **No problem.** The banner shows a fixed healthy state.
- **Nothing checked.** With no result, or only `info` ones, the overall status is `unknown` and
  the banner says that nothing could be checked. See [statuses](probe.md#statuses).
- **Problems.** They are listed from the most to the least severe: `critical`, `warn`, then
  `unknown`. The banner shows the most severe one; `unknown` is orange and `info` does not count.
- **Sorting.** The user can sort problems, for example by problem, source or solution.
- **Languages.** French, English and Spanish, chosen at installation. English is the default.
- **One rendering**, on the server, rather than a second one in the script from the API.
- **No Node.js** and no automated test of the script.
