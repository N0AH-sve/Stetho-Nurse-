# Deployment and acceptance

> Status: to be written.

## Purpose

How to install, check and remove the stack next to Hermes.

## Planned content

- Prerequisites and server checks, including
  [file access and secret files](security.md#decisions)
- Installation, update and removal
- Reverse proxy setup, which must limit what reaches STETHO: see
  [connections](api.md#security). Check that the port is bound to `127.0.0.1` only
- Acceptance scenarios

## Decisions

- **Read-only mount of the Hermes data, to be verified.** STETHO and NURSE get it read-only, and
  a check on the server proves it before they are started: opened with `mode=ro` on a folder it
  may write, SQLite can create or change the `-wal` and `-shm` files of Hermes. **Not verified:**
  that the databases can still be read through such a mount.
