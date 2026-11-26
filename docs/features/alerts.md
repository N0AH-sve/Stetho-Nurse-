# Alerts

> Status: to be written.

## Purpose

Sends one message per incident and one when it is resolved.

## Planned content

- Debouncing and deduplication
- Incident state across restarts
- Channel setup: Discord or Telegram

## Decisions

- **Languages.** French, English and Spanish, chosen at installation. English is the default.
- **Channel.** Notifications are not tied to Discord: Telegram must be possible too. The
  channel is kept separate from the incident logic.
- **First version.** Discord only. Telegram can be added later; it needs a bot token, used only
  to send messages.
