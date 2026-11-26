# Security

> Status: to be written.

## Purpose

Describes the rights of each component, secrets handling and the remaining risks.

## Planned content

- Threat model and trust boundaries
- Container hardening and Docker proxy rules
- Authentication
- Secret masking

## Decisions

- **Accepted risk: Docker request bodies.** The proxy filters method and path, not request
  bodies. A compromised NURSE could ask for a privileged or root `exec` in the Hermes container,
  which may lead to the host. No body filter is added.
- **Outbound traffic.** STETHO's outbound traffic is not restricted.
- **Secret files.** Check on the server that STETHO cannot read the Hermes files that may contain
  secrets. If it can, the mount must be reviewed.
- **Data access.** Check on the server that STETHO and NURSE can read the data they need,
  including the job list and the SQLite databases.
- **Two sources of user names.** The name of the person requesting an action comes from the
  reverse proxy. The names shown for conversations come from `state.db`; they are display data
  and never identify who requests an action.
- **Shared proxy account.** Users share one account on the reverse proxy. The requester recorded
  for an action is that account: the log cannot tell people apart. Identifying them through
  Discord is considered too complicated.
- **Access to STETHO.** It goes through the reverse proxy, with the port bound to `127.0.0.1`
  only. If other users or programs on the server are not trusted, a STETHO password is also set
  (`auth.password`).
- **Secrets in configuration.** Secrets live in the local configuration files. These are never
  committed, are mounted read-only and are readable only by the administrator and the service
  that needs them. Each service gets only its own file.
