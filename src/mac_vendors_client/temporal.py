"""How a history export spells an instant, and how to spell one back at it.

A ``history`` export carries the source database's SCD-2 columns verbatim:
``valid_from`` / ``valid_to`` are text holding ``YYYY-MM-DDTHH:MM:SSZ``, and a
point-in-time lookup compares against them as **strings**::

    valid_from <= :as_of AND (valid_to IS NULL OR valid_to > :as_of)

So an ``as_of`` bound has to be spelled the way the column is, or ``<=``
compares punctuation. ``datetime.isoformat()`` does not: it writes ``+00:00``
where the column writes ``Z`` and appends fractional seconds, and both sort
below ``Z``, so the bound quietly lands on the previous version instead of
raising.

This mirrors ``mac_vendors_db.temporal`` rather than importing it: the client
has no third-party dependencies and the exporter is not one of them. The
spelling is part of the export contract, the same way the column names are.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

__all__ = [
    "STORED_TIMESTAMP_FORMAT",
    "end_of_day",
    "stored_timestamp",
]

#: How the export's timestamp columns spell an instant. Not a display format:
#: it is the sort order the ``WHERE`` clause above depends on.
STORED_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def stored_timestamp(moment: datetime) -> str:
    """``moment`` in UTC, spelled the way the export's timestamp columns are.

    A naive datetime is read as UTC rather than refused: a caller that leaves
    the zone off means the same thing by it, and this data has no other zone.

    Sub-second precision is dropped rather than rounded, which is what the
    comparison needs: an instant half a second into a version belongs to that
    version, and the stored bounds have no fraction to be more precise than.
    """
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime(STORED_TIMESTAMP_FORMAT)


def end_of_day(day: date) -> str:
    """The last instant of ``day``, spelled the same way.

    What "as the export looked on 2020-01-01" means to a point-in-time lookup:
    everything the registry held before the day was over. A bare date compares
    below every timestamp on that date and would answer with the previous
    day's data instead.
    """
    return stored_timestamp(datetime.combine(day, time.max, UTC))


def as_of_bound(value: str | datetime) -> str:
    """Normalize an ``as_of`` argument to the stored spelling.

    A ``datetime`` is spelled by :func:`stored_timestamp`. A string is passed
    through **unchanged**: the caller already chose a spelling, and this is not
    the place to overrule it - a bare ``2020-01-01`` means "from the start of
    that day" to the comparison above, and re-spelling it would move the
    boundary. A caller that wants an instant should hand over the instant.

    A ``date`` is refused rather than guessed at: it has two honest readings,
    and :func:`end_of_day` names the one a point-in-time lookup wants.
    """
    if isinstance(value, str):
        return value
    # datetime is a subclass of date, so it has to be tested first.
    if isinstance(value, datetime):
        return stored_timestamp(value)
    if isinstance(value, date):
        raise TypeError(
            "as_of takes an instant, not a date: a date has two readings, and "
            "end_of_day(day) names the one a point-in-time lookup wants"
        )
    raise TypeError(f"as_of must be a str or datetime, got {type(value).__name__}")
