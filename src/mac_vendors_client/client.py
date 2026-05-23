"""Offline MAC -> vendor lookup against an exported SQLite database.

The database is produced by ``mac-vendors-db``'s SQLite exporter and has a
single ``mac_addresses(assignment, organization_name, organization_address,
range_begin, range_end, bits)`` table plus a ``metadata(key, value)`` table.
Lookups resolve a MAC to the most specific (largest ``bits``) prefix whose
``[range_begin, range_end]`` interval contains the address.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType

from .models import ExportInfo, VendorMatch

# Characters stripped from a MAC before parsing (":", "-", ".", whitespace).
_MAC_SEPARATORS = str.maketrans("", "", ":-. \t\r\n")

_LOOKUP_SQL = (
    "SELECT assignment, organization_name, organization_address, bits "
    "FROM mac_addresses "
    "WHERE range_begin <= ? AND range_end >= ? "
    "ORDER BY bits DESC LIMIT 1"
)


def _mac_to_int(mac: str) -> int:
    """Normalize a MAC address to its 48-bit integer value.

    Accepts any separator style (``00:11:22:...``, ``00-11-22-...``,
    ``0011.2233.4455``, bare hex). A prefix shorter than 12 hex digits is
    left-justified into 48 bits (so an OUI resolves to the start of its range).

    Raises:
        ValueError: if the input is empty, too long, or not valid hex.
    """
    cleaned = mac.translate(_MAC_SEPARATORS).upper()
    if not cleaned:
        raise ValueError("empty MAC address")
    if len(cleaned) > 12:
        raise ValueError(f"MAC address has more than 48 bits: {mac!r}")
    try:
        value = int(cleaned, 16)
    except ValueError:
        raise ValueError(f"invalid MAC address: {mac!r}") from None
    # Left-justify a short prefix to 48 bits (4 bits per missing hex digit).
    return value << (4 * (12 - len(cleaned)))


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

    __slots__ = ("_conn", "_path")

    def __init__(self, path: str | Path) -> None:
        """Open the export database read-only.

        Args:
            path: Path to the exported SQLite file.

        Raises:
            FileNotFoundError: if the file does not exist.
        """
        self._path = Path(path)
        if not self._path.exists():
            raise FileNotFoundError(f"Export database not found: {self._path}")
        # Read-only URI connection; usable from multiple threads (reads only).
        self._conn = sqlite3.connect(
            f"file:{self._path.as_posix()}?mode=ro",
            uri=True,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

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
        row = self._conn.execute(_LOOKUP_SQL, (mac_int, mac_int)).fetchone()
        if row is None:
            return None
        return VendorMatch(
            assignment=row["assignment"],
            organization_name=row["organization_name"] or "",
            organization_address=row["organization_address"] or "",
            bits=row["bits"],
        )

    def lookup_name(self, mac: str) -> str | None:
        """Return only the vendor name for a MAC, or None if unknown."""
        match = self.lookup(mac)
        return match.organization_name if match is not None else None

    def lookup_batch(self, macs: Iterable[str]) -> dict[str, VendorMatch | None]:
        """Look up many MACs, returning a mapping of input -> match (or None).

        Invalid entries map to None rather than raising, so one bad address
        does not abort the batch.
        """
        results: dict[str, VendorMatch | None] = {}
        for mac in macs:
            try:
                results[mac] = self.lookup(mac)
            except ValueError:
                results[mac] = None
        return results

    def info(self) -> ExportInfo:
        """Return the export's metadata (timestamps and record count)."""
        meta = {
            row["key"]: row["value"]
            for row in self._conn.execute("SELECT key, value FROM metadata")
        }
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
