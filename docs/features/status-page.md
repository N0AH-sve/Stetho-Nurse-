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
- **Not assessed.** What was [not assessed](probe.md#result-format) is not listed among the
  problems. Its pill is grey and says "Not assessed".
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
the sort order, the horizontal position of the tables and the keyboard focus on a column title or
a fold are kept, without moving the page. Selected text is not kept. Only a page that carries the
mark of STETHO is accepted.

- **Failed refresh.** After an error, a refusal, an unexpected page or 10 seconds without an
  answer, a warning is shown, the banner turns orange and the pills turn grey,
  so that old data does not look healthy. The warning stays at the top of the screen while
  scrolling. A later success removes it.
- **Stale report.** The server itself shows it as [out of date](api.md#reading-routes).
- **Script not loaded.** **Limit:** if JavaScript is on but the script cannot be loaded or read,
  the page does not refresh and says nothing. When the reader comes back to a tab, the old state
  stays unmarked until the new page arrives, for 10 seconds at most.
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
sorting, refresh keeping the open parts, sort, focus and scroll positions, the warning after a
network error, a 401 or another page, even one with a `content` element, its place while
scrolling, the return to normal, and a 390 pixel wide screen. Repeat this after a
change of the script.

## Open points

- **Old text after a failed refresh.** The banner and the pills keep their words, such as
  "Everything is working" and "OK", under the warning. Only their colour changes. Settle whether
  the script should replace them.
- **Script not loaded.** Settle whether the page should show a notice until the script hides it.

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
- **Countdown between restarts,** with the action buttons: while NURSE would refuse a restart as
  [too soon](actions.md#with-the-buttons), the page shows the time left.
