# HTTP API

> Status: the contract is written. The server, its protections and the reading routes are
> implemented. The routes of the actions are not served yet.

## Purpose

Read access to the state, and later submission of prescriptions.

## Contract

[`stetho/openapi.yaml`](../../stetho/openapi.yaml) is written by hand and is the reference: it
changes first, then the tests, then the code. It describes every request, response and error.
Schemas of the files exchanged with NURSE (`Prescription`, `PrescriptionResult`) are in it too.

| Route | Role | Served |
| --- | --- | --- |
| `GET /health` | The server answers; says nothing about Hermes. No password | Yes |
| `GET /api/v1/status` | Overall state and every sensor | Yes |
| `GET /api/v1/sensors/{sensor_id}` | One sensor | Yes |
| `GET /api/v1/anomalies` | Current anomalies with the time of their reading, filtered by module and code | Yes |
| `GET /api/v1/openapi.json`, `GET /api/docs` | The contract, and a readable page made from it | Yes |
| `GET /api/v1/csrf-token` | Token needed to submit a prescription | Later |
| `POST /api/v1/prescriptions`, `GET /api/v1/prescriptions/{rx_id}` | Ask for an action and follow it | Later |
| `GET /api/v1/actions` | The last 100 actions of NURSE | Later |

A route marked `x-stetho-served: false` in the contract is described and not served. Routes for
sessions and known weaknesses will be added with their modules.

The [status page](status-page.md) and its two files, `/`, `/static/page.css` and
`/static/page.js`, are not part of the API: the contract does not describe them. They have the same
protections.

- **One file.** There is no schema per module: the details of a sensor are always label and value
  pairs.
- **Closed objects.** A response, and a file exchanged with NURSE, holds the described fields
  only. A field with nothing to say is `null` or an empty list, not left out. Only a request may
  leave a field out.
- **Advice.** A sensor and its items have an `advice`: what to do, or `null`. It is part of the
  [result format](probe.md#result-format).
- **Report.** `summary` is the sentence of the banner. A report is **stale** when no new one has
  arrived for more than the longer of three probe intervals, or the sensor time limit plus one
  interval. `stale` is then true, every status is `unknown`, and every summary says that the data
  is out of date. Advice is `null`, actions and details are empty, and the last known anomalies
  are kept: an old healthy sentence or repair button must not stay. Each sensor has `stale` too.
  Before the first pass, the status is `unknown`, `stale` is false, there is no sensor and
  `checked_at` is `null`.
- **Anomalies.** The answer holds `checked_at`, `stale` and the list, so that an empty list
  cannot be read as healthy when nothing was checked. Only the current ones: without history,
  there is no `active` field and no `since` or `active_only` filter. `received_hidden` tells a
  hidden value from an absent one. The reason, exit code and time limit are fields too, `null`
  when they do not apply.
- **Parameters.** An unknown or repeated query parameter is refused with 400. A malformed sensor
  identifier gives 404.
- **Errors.** `code` is stable; `message` is in the language of the installation.
- **Versioning.** Routes are under `/api/v1`. A breaking change opens `/api/v2`, and `v1` stays
  served during the migration.

## Reading routes

`stetho/web/routes.py` answers from the latest report of the [probe](probe.md#probe), kept in
memory: no request runs a sensor. `stetho/web/report.py` shapes the answers.

- **Banner sentence.** "Everything is working" when the status is `ok`. Otherwise, the title and
  summary of the first sensor that has the overall status. When nothing counts, "Nothing could be
  checked".
- **Staleness** is measured with a clock that cannot be set back, from the time a report arrives:
  changing the date of the server hides nothing. With the example settings, a report is stale
  after more than 90 s. Titles, labels, times and anomaly codes are kept.
- **Limits of staleness.** The clock used does not advance while the server is suspended: after
  it resumes, an old report can look fresh until the next one arrives, normally within an
  interval and the time of a pass. When the sensor time limit is more than twice the interval, a
  report may be shown as stale for a moment when a sensor first exceeds its limit.
- **Documentation page.** `/api/docs` is made from the contract when STETHO starts, without
  script, style or link. Its title and labels are in the language of the installation; the
  descriptions come from the contract and are in English.

## Actions

Described now, served later.

- **Request.** It holds the action and an optional target, which are what reaches NURSE. There is
  no free reason.
- **Size.** A request is limited to 4 KiB, like the file NURSE reads, and refused with 413 above
  it. The CSRF token is limited to 256 characters.
- **Requester.** `requested_by` has up to 64 characters and no control character.
- **Refusal.** `refusal_reason` is a short code of up to 128 characters. The codes come with
  NURSE.
- **Checking values.** The patterns of the contract follow the JSON Schema rules, where `$` is the
  very end. Python lets `$` match before a final newline, so a validator written in Python accepts
  `RESTART\n`. The code must check a received value on its whole length (`fullmatch`, with the
  patterns of the code), never with the schema alone. This also applies to NURSE.

## Security

`stetho/web/guard.py` surrounds the whole application, so that a new route is protected by
default. It checks, in this order:

1. **Protocol.** HTTP only. WebSockets are off, since they would skip checks written for HTTP.
2. **Host name**, on every route, `/health` included. The `Host` header, without its port and
   in any case, must be one of `server.allowed_hosts`. This protects against DNS rebinding.
   A missing or repeated header is refused with 400.
3. **Password**, when `auth.password` is set, on everything but `/health`: styles, scripts and
   unknown addresses included, so that the routes are not revealed. HTTP Basic, realm `STETHO`,
   UTF-8. The user name is ignored. Digests of the passwords are compared in constant time. A
   missing, repeated or malformed header and a wrong password get the same 401.
4. **Method.** `GET` and `HEAD` only; any other gets 405, `OPTIONS` included.

There is no redirection for a trailing slash.

- **No password.** Accepted, with a warning at startup: the reverse proxy then authenticates, and
  whoever reaches the port has access. The host check does not replace a password: a program that
  reaches the port can send any host name.
- **Headers**, on every response of the application, errors included, and no route can weaken
  them:
  `Content-Security-Policy: default-src 'none'; style-src 'self'; script-src 'self';
  connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none';
  frame-ancestors 'none'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store`,
  `Cross-Origin-Resource-Policy: same-origin` and `Cross-Origin-Opener-Policy: same-origin`.
  CORS headers are removed. No header names the server software.
- **Proxy.** `X-Forwarded-*` headers are ignored.
- **Log.** Accesses are not logged. A failed authentication writes one line, without user name,
  password, address or path; a request without credentials writes nothing. **Limits:** attempts
  are not slowed down or blocked, their origin is unknown, and many of them fill the log.
- **Errors.** An unexpected error answers 500 without detail. The log keeps its type and its
  calls in STETHO. For the messages of other libraries, such as the web server, only their origin
  and level are written, and no traceback is ever written. This can make a failure of the web
  server harder to understand; a port that cannot be opened has its own message.
- **Limit.** A request that is not valid HTTP, such as an HTTP/1.1 request without a `Host` header
  or with two, is answered by the web server itself, before these checks: that response has no
  security header and is not in the error format. It names no software and repeats nothing of the
  request.
- **Limit.** Anyone who reaches the port can fill the log without a password: an invalid request,
  or one that asks for another protocol, writes a line of the web server, without its message.
- **Connections.** STETHO bounds neither the number of connections nor the time to send the
  headers of a request, and this is accepted: 3 or 4 users at most are expected, on one account.
  A limit of that size would be wrong, since one browser opens several connections. **Limit:**
  this holds only if nobody else reaches the port. Many idle connections, even without a
  password, could use up the files the process may open, which the sensors need too. The port
  must stay bound to `127.0.0.1`, behind the reverse proxy.

**CSRF** (with the actions, not implemented). A token signed with a random key made at startup,
valid for 12 hours, sent in `X-CSRF-Token`. The submission accepts JSON only and refuses a
request that the browser reports as coming from another site. The token is not tied to a person
and ends when STETHO restarts.

## Tests

`tests/stetho/test_openapi.py` checks that the contract is a valid OpenAPI 3.1 document, and
that its routes, protections, errors, enumerations and patterns match the decisions and the code.
`tests/stetho/test_web.py` starts the real server on a local port and checks each protection,
refusals included; `test_main.py` covers the startup and the log. The tests use the standard
library as HTTP client: the test client of Starlette now asks for `httpx2`.

`tests/stetho/test_api.py` calls the application with reports and a clock that it controls. It
checks every JSON response against the contract with `openapi-schema-validator`, formats
included: before the first pass, with each made-up scenario in the three languages, with every
field and anomaly code, for stale reports and for errors. It also checks that the routes marked
as served, and only they, answer.

## Open points

To settle before the [actions](#actions) are served:

- **Error codes and statuses.** The contract does not say which `code` goes with which status.
  `bad_target` has no stated use next to `invalid_request`.
- **Malformed `rx_id`.** Whether `GET /api/v1/prescriptions/{rx_id}` answers 404, as for a
  sensor, or 400.

## Decisions

- Any well-formed action code is accepted; NURSE validates it. See
  [corrective actions](actions.md#decisions).
- **Actions later.** The contract describes the action log and how the CSRF token is obtained,
  but nothing that changes anything is served before NURSE exists.
- **Documentation page.** A simple page made from the contract, because third-party scripts are
  not allowed.
- **History.** The 7-day history of anomalies comes with the incident state of the alerts. The
  Hermes version stays in the `gateway` card.
