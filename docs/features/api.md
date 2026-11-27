# HTTP API

> Status: the contract is written. Nothing is served yet.

## Purpose

Read access to the state, and later submission of prescriptions.

## Contract

[`stetho/openapi.yaml`](../../stetho/openapi.yaml) is written by hand and is the reference: it
changes first, then the tests, then the code. It describes every request, response and error.
Schemas of the files exchanged with NURSE (`Prescription`, `PrescriptionResult`) are in it too.

| Route | Role | Served |
| --- | --- | --- |
| `GET /health` | The server answers; says nothing about Hermes. No password | Planned in this step |
| `GET /api/v1/status` | Overall state and every sensor | Planned in this step |
| `GET /api/v1/sensors/{sensor_id}` | One sensor | Planned in this step |
| `GET /api/v1/anomalies` | Current anomalies with the time of their reading, filtered by module and code | Planned in this step |
| `GET /api/v1/openapi.json`, `GET /api/docs` | The contract, and a readable page made from it | Planned in this step |
| `GET /api/v1/csrf-token` | Token needed to submit a prescription | Later |
| `POST /api/v1/prescriptions`, `GET /api/v1/prescriptions/{rx_id}` | Ask for an action and follow it | Later |
| `GET /api/v1/actions` | The last 100 actions of NURSE | Later |

A route marked `x-stetho-served: false` in the contract is described and not served. Routes for
sessions and known weaknesses will be added with their modules.

- **One file.** There is no schema per module: the details of a sensor are always label and value
  pairs.
- **Closed objects.** A response, and a file exchanged with NURSE, holds the described fields
  only. A field with nothing to say is `null` or an empty list, not left out. Only a request may
  leave a field out.
- **Advice.** A sensor and its items have an `advice`: what to do, or `null`. It is not yet in
  the [result format](probe.md#result-format) of the code.
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

## Actions

Described now, served later.

- **Request.** It holds the action and an optional target, which are what reaches NURSE. There is
  no free reason.
- **Size.** A request is limited to 4 KiB, like the file NURSE reads, and refused with 413 above
  it. The CSRF token is limited to 256 characters.
- **Requester.** `requested_by` has up to 64 characters and no control character.
- **Refusal.** `refusal_reason` is a short code of up to 128 characters. The codes come with
  NURSE.

## Security

Decided, and not implemented yet.

- **Password.** When `auth.password` is set, HTTP Basic protects everything but `/health`,
  styles and scripts included. The user name is ignored and the password is compared in constant
  time. It must have at least 16 characters. Failures are logged without their content. There is
  no lockout, so attempts are not slowed down.
- **No password.** Accepted, with a warning at startup: the reverse proxy then authenticates, and
  whoever reaches the port has access.
- **Host names.** A setting, `server.allowed_hosts`, lists the accepted ones, against DNS
  rebinding. Any other is refused on every route.
- **HTTP protections.** Strict content security policy with resources of STETHO only, no
  framing, `nosniff`, no referrer, `no-store` and no CORS. `X-Forwarded-*` headers are ignored.
  The access log is off. An internal error shows no trace and no message, in responses or logs.
- **CSRF** (with the actions). A token signed with a random key made at startup, valid for 12
  hours, sent in `X-CSRF-Token`. The submission accepts JSON only and refuses a request that the
  browser reports as coming from another site. The token is not tied to a person and ends when
  STETHO restarts.

## Tests

`tests/stetho/test_openapi.py` checks that the contract is a valid OpenAPI 3.1 document, and
that its routes, protections, errors, enumerations and patterns match the decisions and the code.
No response is checked yet. These checks will use `openapi-schema-validator`, which is typed,
instead of `jsonschema`, which is not.

## Decisions

- Any well-formed action code is accepted; NURSE validates it. See
  [corrective actions](actions.md#decisions).
- **Actions later.** The contract describes the action log and how the CSRF token is obtained,
  but nothing that changes anything is served before NURSE exists.
- **Documentation page.** A simple page made from the contract, because third-party scripts are
  not allowed.
- **History.** The 7-day history of anomalies comes with the incident state of the alerts. The
  Hermes version stays in the `gateway` card.
