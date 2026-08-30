"""Offline MAC -> vendor lookup against an exported SQLite database.

The database is a SQLite export with a ``mac_addresses`` table plus a
``metadata(key, value)`` table. Lookups resolve a MAC to the most specific
(largest ``bits``) prefix whose ``[range_begin, range_end]`` interval contains
the address.

The exporter emits several column shapes and the client adapts to whichever it
is handed: the free "minimal" snapshot has no ``organization_address`` and the
licensed client feed appends ``short_name``. Only ``assignment``,
``organization_name``, ``range_begin``, ``range_end`` and ``bits`` are
required; any further column (the full export's vendor enrichment) is ignored.
"""

from __future__ import annotations

import sqlite3
import string
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType

from .models import ExportInfo, VendorMatch

# Characters stripped from a MAC before parsing (":", "-", ".", whitespace).
_MAC_SEPARATORS = str.maketrans("", "", ":-. \t\r\n")

# int(value, 16) is too permissive to double as validation: it also accepts a
# sign, an "0x" prefix, underscores and non-ASCII digits, which would then be
# counted in len(cleaned) and silently shift the address to a wrong value.
_HEX_DIGITS = frozenset(string.hexdigits)

# Columns every export shape carries. Anything else is optional and selected
# as '' when absent, so a row reads the same whatever the export mode was.
_REQUIRED_COLUMNS = ("assignment", "organization_name", "range_begin", "range_end", "bits")
_OPTIONAL_COLUMNS = ("organization_address", "short_name")


def _lookup_sql(columns: frozenset[str]) -> str:
    """Build the lookup query for the columns this export actually has."""
    selected = ", ".join(name if name in columns else f"'' AS {name}" for name in _OPTIONAL_COLUMNS)
    return (
        f"SELECT assignment, organization_name, {selected}, bits "
        "FROM mac_addresses "
        "WHERE range_begin <= ? AND range_end >= ? "
        # Most specific (largest bits) wins; range_begin DESC is a deterministic
        # tie-breaker so overlapping equal-bits rows resolve to a stable result.
        "ORDER BY bits DESC, range_begin DESC LIMIT 1"
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

    __slots__ = ("_conn", "_path", "_sql")

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
            self._sql = _lookup_sql(self._validated_columns())
        except sqlite3.DatabaseError:
            self._conn.close()
            raise

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

    def lookup(self, mac: str) -> VendorMatch | None:
        """Resolve a MAC address to its vendor, or None if unknown.

        Args:
            mac: MAC address in any common format (separators optional). A
                shorter prefix (e.g. a 6-hex OUI) is also accepted.

        Returns:
            The most specific matching VendorMatch, or None.

        Raises:
            ValueError: if ``mac`` is not a valid (partial) MAC address.
        """
        mac_int = _mac_to_int(mac)
        row = self._conn.execute(self._sql, (mac_int, mac_int)).fetchone()
        if row is None:
            return None
        return VendorMatch(
            assignment=row["assignment"],
            organization_name=row["organization_name"] or "",
            # The SQL supplies '' for a column this export lacks; `or ""`
            # covers a column that is there but NULL for this row.
            organization_address=row["organization_address"] or "",
            bits=row["bits"],
            short_name=row["short_name"] or "",
        )

    def lookup_name(self, mac: str) -> str | None:
        """Return the vendor's ``organization_name`` for a MAC, or None.

        For the short brand name where the export carries one, use
        ``lookup(...).display_name``.
        """
        match = self.lookup(mac)
        return match.organization_name if match is not None else None

    def lookup_batch(self, macs: Iterable[str]) -> dict[str, VendorMatch | None]:
        """Look up many MACs, returning a mapping of input string -> match (or None).

        Invalid MAC inputs map to None rather than raising, so one malformed
        address does not abort the batch. Results are keyed by the input
        string, so a repeated input collapses to a single entry and costs a
        single query. (Database-level errors are not swallowed - they
        propagate.)
        """
        results: dict[str, VendorMatch | None] = {}
        for mac in macs:
            if mac in results:  # a repeated input needs no second query
                continue
            try:
                results[mac] = self.lookup(mac)
            except ValueError:
                results[mac] = None
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
