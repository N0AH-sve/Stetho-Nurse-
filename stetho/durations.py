"""Durations as short texts, without plural forms."""

from datetime import timedelta

from stetho.i18n import Translator


def duration_text(duration: timedelta, translator: Translator) -> str:
    """The largest whole unit, rounded down. A negative duration counts as zero."""
    seconds = max(int(duration.total_seconds()), 0)
    for unit, size in (("days", 86400), ("hours", 3600), ("minutes", 60)):
        if seconds >= size:
            return translator.text(f"duration.{unit}", count=seconds // size)
    return translator.text("duration.seconds", count=seconds)
