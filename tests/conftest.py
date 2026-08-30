"""Test fixtures: build export-format SQLite databases.

The schema mirrors the SQLite export format exactly (mac_addresses +
metadata) so the client is tested against the real contract. The exporter
emits several column shapes; ``make_export_db`` builds any of them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

# Column -> DDL type, in the order the exporter declares them.
_COLUMN_TYPES = {
    "assignment": "TEXT NOT NULL",
    "organization_name": "TEXT",
    "organization_address": "TEXT",
    "range_begin": "INTEGER NOT NULL",
    "range_end": "INTEGER NOT NULL",
    "bits": "INTEGER NOT NULL",
    "short_name": "TEXT",
}

#: The default export: everything but the feed's short_name.
DEFAULT_COLUMNS = tuple(name for name in _COLUMN_TYPES if name != "short_name")
#: The free monthly snapshot ("minimal" mode) drops the address column.
MINIMAL_COLUMNS = tuple(name for name in DEFAULT_COLUMNS if name != "organization_address")
#: The licensed client feed ("short_names" mode) appends short_name.
FEED_COLUMNS = (*DEFAULT_COLUMNS, "short_name")

# (assignment, org_name, org_address, bits, short_name)
_RECORDS = [
    ("005056", "VMware, Inc.", "Palo Alto CA US", 24, "VMware"),  # MA-L
    ("001122", "Broad Vendor", "Somewhere US", 24, ""),  # MA-L, contains the MA-S below
    ("001122000", "Specific Vendor", "Elsewhere US", 36, "Specific"),  # MA-S, subset of 001122
    ("0011220", "Medium Vendor", "Midtown US", 28, "Medium"),  # MA-M
]

DEFAULT_METADATA = {
    "updated_at": "2026-01-01T00:00:00Z",
    "exported_at": "2026-01-02T00:00:00Z",
    "total_records": str(len(_RECORDS)),
}


def _range(assignment_hex: str, bits: int) -> tuple[int, int]:
    """Reproduce the exporter's assignment -> [range_begin, range_end]."""
    begin = int(assignment_hex.ljust(12, "0"), 16)
    end = begin | ((1 << (48 - bits)) - 1)
    return begin, end


def make_export_db(
    path: Path,
    columns: Sequence[str] = DEFAULT_COLUMNS,
    metadata: Mapping[str, str] | None = DEFAULT_METADATA,
) -> Path:
    """Write an export database holding `columns` and return its path.

    The exporter always writes the metadata table, whatever the mode;
    ``metadata=None`` omits it to exercise the client's tolerance of an export
    that has lost it.
    """
    ddl = ", ".join(f"{name} {_COLUMN_TYPES[name]}" for name in columns)
    rows = []
    for assignment, name, address, bits, short_name in _RECORDS:
        begin, end = _range(assignment, bits)
        values = {
            "assignment": assignment,
            "organization_name": name,
            "organization_address": address,
            "range_begin": begin,
            "range_end": end,
            "bits": bits,
            "short_name": short_name,
        }
        rows.append(tuple(values[column] for column in columns))

    conn = sqlite3.connect(path)
    try:
        conn.execute(f"CREATE TABLE mac_addresses (id INTEGER PRIMARY KEY, {ddl})")
        conn.executemany(
            f"INSERT INTO mac_addresses ({', '.join(columns)}) "
            f"VALUES ({', '.join('?' * len(columns))})",
            rows,
        )
        if metadata is not None:
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
            conn.executemany("INSERT INTO metadata (key, value) VALUES (?, ?)", metadata.items())
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture
def export_db(tmp_path: Path) -> Path:
    """The default export shape."""
    return make_export_db(tmp_path / "vendors.db")


@pytest.fixture
def minimal_export_db(tmp_path: Path) -> Path:
    """The free monthly snapshot: no ``organization_address`` column."""
    return make_export_db(tmp_path / "minimal.db", MINIMAL_COLUMNS)


@pytest.fixture
def client_feed_db(tmp_path: Path) -> Path:
    """The licensed client feed: the default columns plus ``short_name``."""
    return make_export_db(tmp_path / "feed.db", FEED_COLUMNS)
