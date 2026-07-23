"""Test fixtures: build a minimal export-format SQLite database.

The schema mirrors the SQLite export format exactly (mac_addresses +
metadata) so the client is tested against the real contract.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest


def _range(assignment_hex: str, bits: int) -> tuple[int, int]:
    """Reproduce the exporter's assignment -> [range_begin, range_end]."""
    begin = int(assignment_hex.ljust(12, "0"), 16)
    end = begin | ((1 << (48 - bits)) - 1)
    return begin, end


# (assignment, org_name, org_address, bits)
_RECORDS = [
    ("005056", "VMware, Inc.", "Palo Alto CA US", 24),  # MA-L
    ("001122", "Broad Vendor", "Somewhere US", 24),  # MA-L, contains the MA-S below
    ("001122000", "Specific Vendor", "Elsewhere US", 36),  # MA-S, subset of 001122
    ("0011220", "Medium Vendor", "Midtown US", 28),  # MA-M
]


@pytest.fixture
def export_db(tmp_path: Path) -> Iterator[Path]:
    """Create an exported SQLite database and yield its path."""
    db_path = tmp_path / "vendors.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE mac_addresses ("
            "id INTEGER PRIMARY KEY, assignment TEXT NOT NULL, "
            "organization_name TEXT, organization_address TEXT, "
            "range_begin INTEGER NOT NULL, range_end INTEGER NOT NULL, bits INTEGER NOT NULL)"
        )
        conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        for assignment, name, address, bits in _RECORDS:
            begin, end = _range(assignment, bits)
            conn.execute(
                "INSERT INTO mac_addresses "
                "(assignment, organization_name, organization_address, range_begin, range_end, bits) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (assignment, name, address, begin, end, bits),
            )
        conn.executemany(
            "INSERT INTO metadata (key, value) VALUES (?, ?)",
            [
                ("updated_at", "2026-01-01T00:00:00Z"),
                ("exported_at", "2026-01-02T00:00:00Z"),
                ("total_records", str(len(_RECORDS))),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    yield db_path
