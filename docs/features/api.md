# HTTP API

> Status: to be written.

## Purpose

Read access to the state, and submission of prescriptions.

## Planned content

- Routes and the OpenAPI contract
- Authentication and CSRF protection
- Versioning and contract tests

## Decisions

- Any well-formed action code is accepted; NURSE validates it. See
  [corrective actions](actions.md#decisions).
- **Contract completion.** It must describe at least the items of a sensor, the action log and
  how the CSRF token is obtained. It is then versioned in the repository, as the reference.
