"""Offline MAC -> vendor lookup against an exported SQLite database.

The database is a SQLite export with a ``mac_addresses`` table plus a
``metadata(key, value)`` table. Lookups resolve a MAC to the most specific
(largest ``bits``) prefix whose ``[range_begin, range_end]`` interval contains
the address.

The exporter emits several column shapes and the client adapts to whichever it
is handed: the free "minimal" snapshot has no ``organization_address``, the
licensed client feed appends ``short_name``, the full export appends the rest
of the vendor enrichment, and a ``history`` export appends the SCD-2
``valid_from`` / ``valid_to`` columns. Only ``assignment``,
``organization_name``, ``range_begin``, ``range_end`` and ``bits`` are
required; a column the export lacks reads as blank.
"""

from __future__ import annotations

import sqlite3
import string
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from types import TracebackType

from .models import ExportInfo, VendorMatch
from .temporal import as_of_bound

# Characters stripped from a MAC before parsing (":", "-", ".", whitespace).
_MAC_SEPARATORS = str.maketrans("", "", ":-. \t\r\n")

# int(value, 16) is too permissive to double as validation: it also accepts a
# sign, an "0x" prefix, underscores and non-ASCII digits, which would then be
# counted in len(cleaned) and silently shift the address to a wrong value.
_HEX_DIGITS = frozenset(string.hexdigits)

# Columns every export shape carries.
_REQUIRED_COLUMNS = ("assignment", "organization_name", "range_begin", "range_end", "bits")

# Columns only some export modes carry, and what the query selects in place of
# one the mode left out. Substituting a literal keeps a single row shape for
# every mode, so nothing downstream branches on which export it was handed.
_OPTIONAL_COLUMNS = {
    "organization_address": "''",
    "short_name": "''",
    "country_code": "''",
    "assignment_count": "0",
    "registries": "''",
    "first_seen": "''",
    "last_seen": "''",
}

# Present together, and only in a `history` export: that mode keeps every
# version of an assignment rather than one row per assignment.
_TEMPORAL_COLUMNS = ("valid_from", "valid_to")

# A history export covers the same range once per version, so the plain
# most-specific-wins query can resolve to a superseded row. NULL marks the
# version that is current.
_CURRENT_ROW = " AND valid_to IS NULL"

# The same range, as it stood at an instant. Half-open, so a bound exactly on a
# change belongs to the version that starts there.
_ROW_AS_OF = " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"


def _lookup_sql(columns: frozenset[str], temporal: str = "") -> str:
    """Build the lookup query for the columns this export actually has."""
    selected = ", ".join(
        name if name in columns else f"{default} AS {name}"
        for name, default in _OPTIONAL_COLUMNS.items()
    )
    return (
        f"SELECT assignment, organization_name, {selected}, bits "
        "FROM mac_addresses "
        f"WHERE range_begin <= ? AND range_end >= ?{temporal} "
        # Most specific (largest bits) wins; range_begin DESC is a deterministic
        # tie-breaker so overlapping equal-bits rows resolve to a stable result.
        "ORDER BY bits DESC, range_begin DESC LIMIT 1"
    )


def _match(row: sqlite3.Row | None) -> VendorMatch | None:
    """The vendor a result row names, or None when the lookup found nothing.

    Every export mode produces the same row shape - the query substitutes a
    blank for a column the mode lacks - so this reads one way for all of them.
    """
    if row is None:
        return None
    return VendorMatch(
        assignment=row["assignment"],
        organization_name=row["organization_name"] or "",
        # The SQL substitutes a blank for a column this export lacks; the `or`
        # covers a column that is there but NULL for this row.
        organization_address=row["organization_address"] or "",
        bits=row["bits"],
        short_name=row["short_name"] or "",
        country_code=row["country_code"] or "",
        assignment_count=row["assignment_count"] or 0,
        registries=row["registries"] or "",
        first_seen=row["first_seen"] or "",
        last_seen=row["last_seen"] or "",
    )


def _mac_to_int(mac: str) -> int:
    """Normalize a MAC address to its 48-bit integer value.

    Accepts any separator style (``00:11:22:...``, ``00-11-22-...``,
    ``0011.2233.4455``, bare hex). A prefix shorter than 12 hex digits is
    left-justified into 48 bits (so an OUI resolves to the start of its range).

    Raises:
        ValueError: if the input is empty, too long, or not valid hex.
    """
    # No case folding: _HEX_DIGITS holds both cases and int(x, 16) is
    # case-insensitive, so an upper() copy would be pure per-lookup waste.
    cleaned = mac.translate(_MAC_SEPARATORS)
    if not cleaned:
        raise ValueError("empty MAC address")
    if not _HEX_DIGITS.issuperset(cleaned):
        raise ValueError(f"invalid MAC address: {mac!r}")
    if len(cleaned) > 12:
        raise ValueError(f"MAC address has more than 48 bits: {mac!r}")
    # Left-justify a short prefix to 48 bits (4 bits per missing hex digit).
    return int(cleaned, 16) << (4 * (12 - len(cleaned)))


class MacVendorsClient:
    """Read-only client over an exported MAC vendor SQLite database.

    Example:
        with MacVendorsClient("vendors.db") as client:
            match = client.lookup("00:50:56:AA:BB:CC")
            if match:
                print(match.organization_name)

    The database is opened read-only; the file is never modified. Instances
    are cheap; reuse one for many lookups and close it when done (or use the
    context manager).
    """

    __slots__ = ("_as_of_sql", "_conn", "_path", "_sql")

    def __init__(self, path: str | Path) -> None:
        """Open the export database read-only.

        Args:
            path: Path to the exported SQLite file.

        Raises:
            FileNotFoundError: if the path does not exist or is not a file.
            sqlite3.DatabaseError: if the file is not a valid SQLite database,
                or is not a MAC vendor export.
        """
        self._path = Path(path)
        if not self._path.is_file():
            raise FileNotFoundError(f"Export database not found: {self._path}")
        # Read-only connection. as_uri() percent-encodes the path so spaces and
        # URI-significant characters ('#', '?', '%') survive intact.
        # check_same_thread=False lets the client be used from a thread other
        # than the one that created it; it does NOT make concurrent use safe -
        # use one client per thread (or serialize) for parallel lookups.
        self._conn = sqlite3.connect(
            f"{self._path.resolve().as_uri()}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        # Fail fast with a clear error if the file is not a SQLite database
        # (sqlite3.connect is lazy and would otherwise only error on first use).
        try:
            self._conn.execute("PRAGMA schema_version")
            columns = self._validated_columns()
        except sqlite3.DatabaseError:
            self._conn.close()
            raise
        # Only a history export can be asked about an instant, and only it needs
        # the current-row filter; every other mode already holds one row per
        # assignment, and filtering on a column it lacks would be an error.
        history = all(column in columns for column in _TEMPORAL_COLUMNS)
        self._sql = _lookup_sql(columns, _CURRENT_ROW if history else "")
        self._as_of_sql = _lookup_sql(columns, _ROW_AS_OF) if history else None

    def _validated_columns(self) -> frozenset[str]:
        """The columns of ``mac_addresses``, refusing a database without them.

        Turns a foreign database into an error at construction rather than an
        opaque "no such column" on the first lookup.
        """
        columns = frozenset(
            str(row["name"]) for row in self._conn.execute("PRAGMA table_info(mac_addresses)")
        )
        missing = [name for name in _REQUIRED_COLUMNS if name not in columns]
        if missing:
            detail = (
                "no mac_addresses table"
                if not columns
                else f"mac_addresses is missing {', '.join(missing)}"
            )
            raise sqlite3.DatabaseError(f"Not a MAC vendor export ({detail}): {self._path}")
        return columns

    @property
    def has_history(self) -> bool:
        """True for a ``history`` export, the one mode that keeps old versions.

        Such an export holds every version of an assignment, so a lookup can be
        asked about an instant (``as_of``); it answers with the current version
        otherwise. Every other mode holds one row per assignment, has nothing
        to date, and rejects ``as_of``.
        """
        return self._as_of_sql is not None

    def _statement(self, as_of: str | datetime | None) -> tuple[str, tuple[str, ...]]:
        """The statement this export answers with, and the parameters ``as_of`` adds.

        Resolved once per lookup - or once per batch - so an export that cannot
        answer an ``as_of`` says so before a single address is read, and a
        datetime is spelled once however many addresses follow.
        """
        if as_of is None:
            return self._sql, ()
        if self._as_of_sql is None:
            raise ValueError(
                "as_of needs a history export: this one holds a single row per "
                "assignment, so it has no other version to answer with"
            )
        bound = as_of_bound(as_of)
        return self._as_of_sql, (bound, bound)

    def __del__(self) -> None:
        # Best-effort cleanup if the caller never closed the client and did not
        # use the context manager. Guarded for partial construction.
        conn = getattr(self, "_conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    def __enter__(self) -> MacVendorsClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def lookup(self, mac: str, *, as_of: str | datetime | None = None) -> VendorMatch | None:
        """Resolve a MAC address to its vendor, or None if unknown.

        Args:
            mac: MAC address in any common format (separators optional). A
                shorter prefix (e.g. a 6-hex OUI) is also accepted.
            as_of: Resolve the address as the registry stood at this instant,
                rather than now. A ``datetime`` is spelled the way the export's
                timestamp columns are; a string is used as given. Needs a
                ``history`` export (see ``has_history``); for a whole calendar
                day, pass ``end_of_day(day)``.

        Returns:
            The most specific matching VendorMatch, or None.

        Raises:
            ValueError: if ``mac`` is not a valid (partial) MAC address, or
                ``as_of`` was given for an export that keeps no versions.
            TypeError: if ``as_of`` is a ``date`` or another non-instant.
        """
        sql, dated = self._statement(as_of)
        mac_int = _mac_to_int(mac)
        return _match(self._conn.execute(sql, (mac_int, mac_int, *dated)).fetchone())

    def lookup_name(self, mac: str, *, as_of: str | datetime | None = None) -> str | None:
        """Return the vendor's ``organization_name`` for a MAC, or None.

        For the short brand name where the export carries one, use
        ``lookup(...).display_name``. ``as_of`` is as in :meth:`lookup`.
        """
        match = self.lookup(mac, as_of=as_of)
        return match.organization_name if match is not None else None

    def lookup_batch(
        self, macs: Iterable[str], *, as_of: str | datetime | None = None
    ) -> dict[str, VendorMatch | None]:
        """Look up many MACs, returning a mapping of input string -> match (or None).

        Invalid MAC inputs map to None rather than raising, so one malformed
        address does not abort the batch. Results are keyed by the input
        string, so a repeated input collapses to a single entry and costs a
        single query. (Database-level errors are not swallowed - they
        propagate.) ``as_of`` is as in :meth:`lookup` and applies to the whole
        batch; a bad one raises rather than mapping every address to None.
        """
        sql, dated = self._statement(as_of)
        results: dict[str, VendorMatch | None] = {}
        for mac in macs:
            if mac in results:  # a repeated input needs no second query
                continue
            try:
                mac_int = _mac_to_int(mac)
            except ValueError:
                # Only the parse is guarded, and only here: a malformed address
                # must not abort the batch, but nothing else may be quietly
                # turned into a miss.
                results[mac] = None
                continue
            results[mac] = _match(self._conn.execute(sql, (mac_int, mac_int, *dated)).fetchone())
        return results

    def info(self) -> ExportInfo:
        """Return the export's metadata (timestamps and record count).

        Missing keys - or a missing ``metadata`` table - yield None / 0 fields.
        """
        try:
            rows = self._conn.execute("SELECT key, value FROM metadata").fetchall()
        except sqlite3.OperationalError:
            rows = []
        meta = {row["key"]: row["value"] for row in rows}
        total_raw = meta.get("total_records")
        try:
            total = int(total_raw) if total_raw is not None else 0
        except ValueError:
            total = 0
        return ExportInfo(
            updated_at=meta.get("updated_at"),
            exported_at=meta.get("exported_at"),
            total_records=total,
        )
