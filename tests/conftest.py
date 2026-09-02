"""Test fixtures: build export-format SQLite databases.

The schema mirrors the SQLite export format exactly (mac_addresses +
metadata) so the client is tested against the real contract. The exporter
emits several column shapes; ``make_export_db`` builds any of them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

# Column -> DDL type, in the order the exporter declares them. The temporal
# pair comes last because the exporter appends it in place of the enrichment,
# never alongside it.
_COLUMN_TYPES = {
    "assignment": "TEXT NOT NULL",
    "organization_name": "TEXT",
    "organization_address": "TEXT",
    "range_begin": "INTEGER NOT NULL",
    "range_end": "INTEGER NOT NULL",
    "bits": "INTEGER NOT NULL",
    "short_name": "TEXT",
    "country_code": "TEXT",
    "assignment_count": "INTEGER",
    "registries": "TEXT",
    "first_seen": "TEXT",
    "last_seen": "TEXT",
    "valid_from": "TEXT",
    "valid_to": "TEXT",
}

#: The default export: the per-assignment columns and nothing else.
DEFAULT_COLUMNS = (
    "assignment",
    "organization_name",
    "organization_address",
    "range_begin",
    "range_end",
    "bits",
)
#: The free monthly snapshot ("minimal" mode) drops the address column.
MINIMAL_COLUMNS = tuple(name for name in DEFAULT_COLUMNS if name != "organization_address")
#: The licensed client feed ("short_names" mode) appends short_name.
FEED_COLUMNS = (*DEFAULT_COLUMNS, "short_name")
#: The full export ("enriched" mode) appends the vendor-level columns.
ENRICHED_COLUMNS = (
    *FEED_COLUMNS,
    "country_code",
    "assignment_count",
    "registries",
    "first_seen",
    "last_seen",
)
#: The SCD-2 dump ("history" mode) appends the temporal pair instead, and holds
#: every version of an assignment rather than one row per assignment.
HISTORY_COLUMNS = (*DEFAULT_COLUMNS, "valid_from", "valid_to")

# What a column reads as for a record that does not set it, by DDL type. The
# exporter writes '' for an unmatched vendor rather than leaving it NULL.
_BLANK: dict[str, Any] = {"TEXT": "", "INTEGER": 0}

_RECORDS: list[dict[str, Any]] = [
    {  # MA-L, with the full enrichment payload
        "assignment": "005056",
        "organization_name": "VMware, Inc.",
        "organization_address": "Palo Alto CA US",
        "bits": 24,
        "short_name": "VMware",
        "country_code": "US",
        "assignment_count": 3,
        "registries": "MA-L,MA-S",
        "first_seen": "2004-06-11T00:00:00Z",
        "last_seen": "2026-01-01T00:00:00Z",
    },
    {  # MA-L, contains the MA-S below; no vendors row, so no enrichment
        "assignment": "001122",
        "organization_name": "Broad Vendor",
        "organization_address": "Somewhere US",
        "bits": 24,
    },
    {  # MA-S, a subset of 001122
        "assignment": "001122000",
        "organization_name": "Specific Vendor",
        "organization_address": "Elsewhere US",
        "bits": 36,
        "short_name": "Specific",
        "country_code": "DE",
        "assignment_count": 1,
        "registries": "MA-S",
        "first_seen": "2015-02-03T00:00:00Z",
        "last_seen": "2015-02-03T00:00:00Z",
    },
    {  # MA-M
        "assignment": "0011220",
        "organization_name": "Medium Vendor",
        "organization_address": "Midtown US",
        "bits": 28,
        "short_name": "Medium",
    },
]

#: When every assignment in the history fixture was first recorded.
RECORDED_AT = "2000-03-15T00:00:00Z"
#: When 005056 changed its name. Half-open: the instant itself belongs to the
#: version that starts there, not to the one it ends.
CHANGED_AT = "2010-06-01T00:00:00Z"

_HISTORY_RECORDS: list[dict[str, Any]] = [
    # The superseded version of 005056, written first so that a query without
    # the current-row filter would resolve to it: it shares its bits and its
    # range_begin with the current version, so the ORDER BY cannot separate the
    # two and SQLite answers with whichever row it reaches first.
    {
        "assignment": "005056",
        "organization_name": "VMWARE INC",  # the name as it was recorded then
        "organization_address": "Palo Alto CA US",
        "bits": 24,
        "valid_from": RECORDED_AT,
        "valid_to": CHANGED_AT,
    },
    # Then the current version of every record. Only 005056 has ever changed.
    *(
        {
            **record,
            "valid_from": CHANGED_AT if record["assignment"] == "005056" else RECORDED_AT,
            "valid_to": None,
        }
        for record in _RECORDS
    ),
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
    records: Sequence[Mapping[str, Any]] | None = None,
) -> Path:
    """Write an export database holding `columns` and return its path.

    The exporter always writes the metadata table, whatever the mode;
    ``metadata=None`` omits it to exercise the client's tolerance of an export
    that has lost it.
    """
    ddl = ", ".join(f"{name} {_COLUMN_TYPES[name]}" for name in columns)
    rows = []
    for record in records if records is not None else _RECORDS:
        # A column the record does not set reads as blank, the way the exporter
        # writes an unmatched vendor - except valid_to, where NULL is not a
        # blank but the mark of the current version.
        values: dict[str, Any] = {name: _BLANK[_COLUMN_TYPES[name].split()[0]] for name in columns}
        values["valid_to"] = None
        values.update(record)
        values["range_begin"], values["range_end"] = _range(values["assignment"], values["bits"])
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


@pytest.fixture
def enriched_export_db(tmp_path: Path) -> Path:
    """The full export: the default columns plus the vendor-level ones."""
    return make_export_db(tmp_path / "enriched.db", ENRICHED_COLUMNS)


@pytest.fixture
def history_export_db(tmp_path: Path) -> Path:
    """The SCD-2 dump: every version of an assignment, with its validity."""
    return make_export_db(tmp_path / "history.db", HISTORY_COLUMNS, records=_HISTORY_RECORDS)
