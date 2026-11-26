# Corrective actions (NURSE)

> Status: to be written.

## Purpose

Runs only the actions declared in its catalogue, after checking each request. The first
catalogue contains five actions.

## Design

- **Monolith.** NURSE is a single, small program. It has no modules and no plugin system.
- **Easy to extend.** Adding an action means adding an entry to `actions.toml`, with no code
  change, then restarting NURSE.
- **Code is needed only for a new capability**, such as a new way to check that a target exists
  or to verify the effect of an action.
- **Closed at runtime.** An action that is not in the catalogue is refused. Only the
  administrator edits the catalogue; STETHO can neither read nor change it.
- **Fixed guardrails** that no catalogue entry can lift: only the configured container, no shell
  started by NURSE, never as root.

## Decisions

- **Action list for the buttons.** NURSE publishes a summary in the results directory, which
  STETHO can only read: code, label and target type of each action, without the commands.
- **Action codes in the API.** The API accepts any well-formed code. NURSE refuses a code that
  is not in its catalogue.
- **Shells in the catalogue.** NURSE refuses to start if an entry calls a shell, such as `sh -c`.
  This prevents mistakes; it does not make a maliciously edited catalogue safe, so the catalogue
  must be protected and reviewed like code.
- **Rate limits.** Configurable for each action. Defaults per hour: 2 for automatic runs and 5
  for manual runs.
- **Automatic requests.** NURSE accepts a request marked automatic only if the catalogue allows
  it for that action. A total hourly cap applies whatever the origin, because STETHO could lie
  about it.
- **Two timeouts.** The command and the verification of its effect have separate timeouts.
- **Shared directories.** STETHO and NURSE run as different users and share a group with limited
  rights.

## Planned content

- Prescription and result formats
- Checks, in order
- The five actions and their verification
- Manual and automatic modes, rate limits

## Open points

- Default value of the total hourly cap.
