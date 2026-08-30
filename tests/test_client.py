"""Tests for MacVendorsClient and MAC normalization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from conftest import make_export_db

from mac_vendors_client import ExportInfo, MacVendorsClient, VendorMatch
from mac_vendors_client.client import _mac_to_int


class TestMacToInt:
    def test_colon_separated(self) -> None:
        assert _mac_to_int("00:50:56:AA:BB:CC") == 0x005056AABBCC

    def test_hyphen_and_dot_and_bare(self) -> None:
        expected = 0x005056AABBCC
        assert _mac_to_int("00-50-56-AA-BB-CC") == expected
        assert _mac_to_int("0050.56AA.BBCC") == expected
        assert _mac_to_int("005056aabbcc") == expected

    def test_short_prefix_is_left_justified(self) -> None:
        # A 6-hex OUI becomes the start of its 48-bit range.
        assert _mac_to_int("005056") == 0x005056000000

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "  ",
            "ZZ:ZZ",
            "00:50:56:GG",
            "0050560011223344",
            # int(x, 16) accepts all of these; each would otherwise be counted
            # in the length and silently produce a wrong 48-bit value.
            "+005056",
            "00_50_56",
            "0x5056",
            "\u0660\u0661\u0662",  # Arabic-Indic digits
        ],
    )
    def test_invalid_raises(self, bad: str) -> None:
        with pytest.raises(ValueError):
            _mac_to_int(bad)


class TestLookup:
    def test_lookup_found(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            match = client.lookup("00:50:56:AA:BB:CC")
        assert match is not None
        assert match.organization_name == "VMware, Inc."
        assert match.assignment == "005056"
        assert match.bits == 24

    def test_lookup_not_found(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            assert client.lookup("FF:FF:FF:00:00:00") is None

    def test_separator_insensitive(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            a = client.lookup("0050.56aa.bbcc")
            b = client.lookup("005056AABBCC")
        assert a == b == VendorMatch("005056", "VMware, Inc.", "Palo Alto CA US", 24)

    def test_most_specific_wins(self, export_db: Path) -> None:
        # 001122000xxx is inside both the 24-bit 001122 and the 36-bit 001122000;
        # the more specific (36-bit) match must win.
        with MacVendorsClient(export_db) as client:
            match = client.lookup("00:11:22:00:0A:BC")
        assert match is not None
        assert match.organization_name == "Specific Vendor"
        assert match.bits == 36

    def test_broad_match_when_outside_specific(self, export_db: Path) -> None:
        # 0011223xxxxx is inside 001122 (24-bit) but NOT 001122000 (36-bit).
        with MacVendorsClient(export_db) as client:
            match = client.lookup("00:11:22:30:00:00")
        assert match is not None
        assert match.organization_name == "Broad Vendor"
        assert match.bits == 24

    def test_lookup_name(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            assert client.lookup_name("00:50:56:00:00:01") == "VMware, Inc."
            assert client.lookup_name("FF:FF:FF:FF:FF:FF") is None

    def test_lookup_by_oui_prefix(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            match = client.lookup("005056")
        assert match is not None
        assert match.organization_name == "VMware, Inc."

    def test_invalid_mac_raises(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            with pytest.raises(ValueError):
                client.lookup("not-a-mac-zz")


class TestExportShapes:
    """The exporter emits several column shapes; all must resolve a MAC."""

    def test_minimal_export_has_no_address(self, minimal_export_db: Path) -> None:
        # The free snapshot drops organization_address; a lookup must still
        # work and report an empty address rather than raising OperationalError.
        with MacVendorsClient(minimal_export_db) as client:
            match = client.lookup("00:50:56:AA:BB:CC")
        assert match is not None
        assert match.organization_name == "VMware, Inc."
        assert match.organization_address == ""
        assert match.short_name == ""
        assert match.display_name == "VMware, Inc."

    def test_client_feed_carries_short_name(self, client_feed_db: Path) -> None:
        with MacVendorsClient(client_feed_db) as client:
            match = client.lookup("00:50:56:AA:BB:CC")
            fallback = client.lookup("00:11:22:30:00:00")
        assert match is not None
        assert match.short_name == "VMware"
        assert match.display_name == "VMware"
        assert match.organization_name == "VMware, Inc."
        # An empty short_name falls back to the full organization name.
        assert fallback is not None
        assert fallback.short_name == ""
        assert fallback.display_name == "Broad Vendor"

    def test_default_export_has_no_short_name(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            match = client.lookup("00:50:56:AA:BB:CC")
        assert match is not None
        assert match.short_name == ""
        assert match.display_name == "VMware, Inc."


class TestBatch:
    def test_batch_mixed(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            results = client.lookup_batch(["00:50:56:AA:BB:CC", "FF:FF:FF:00:00:00", "bad-zz"])
        assert results["00:50:56:AA:BB:CC"] is not None
        assert results["00:50:56:AA:BB:CC"].organization_name == "VMware, Inc."
        assert results["FF:FF:FF:00:00:00"] is None
        assert results["bad-zz"] is None  # invalid -> None, does not abort batch

    def test_batch_queries_each_input_once(self, export_db: Path) -> None:
        # A repeated input collapses to one entry and costs one query.
        statements: list[str] = []
        with MacVendorsClient(export_db) as client:
            client._conn.set_trace_callback(statements.append)
            results = client.lookup_batch(["005056", "005056", "005056"])
        assert list(results) == ["005056"]
        assert sum("mac_addresses" in sql for sql in statements) == 1


class TestInfo:
    def test_info(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            info = client.info()
        assert info == ExportInfo(
            updated_at="2026-01-01T00:00:00Z",
            exported_at="2026-01-02T00:00:00Z",
            total_records=4,
        )

    def test_info_tolerates_bad_total_records(self, tmp_path: Path) -> None:
        db_path = make_export_db(tmp_path / "bad.db", metadata={"total_records": "not-a-number"})
        with MacVendorsClient(db_path) as client:
            info = client.info()
        assert info.total_records == 0
        assert info.updated_at is None

    def test_info_missing_metadata_table(self, tmp_path: Path) -> None:
        db_path = make_export_db(tmp_path / "nometa.db", metadata=None)
        with MacVendorsClient(db_path) as client:
            assert client.info() == ExportInfo(updated_at=None, exported_at=None, total_records=0)


class TestOpen:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            MacVendorsClient(tmp_path / "nope.db")

    def test_directory_path_raises_file_not_found(self, tmp_path: Path) -> None:
        # A directory exists() but is not a file; must not become an opaque
        # OperationalError.
        with pytest.raises(FileNotFoundError):
            MacVendorsClient(tmp_path)

    def test_non_sqlite_file_raises_database_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "garbage.db"
        bad.write_bytes(b"this is not a sqlite database")
        # Fail fast at construction, not lazily on first query.
        with pytest.raises(sqlite3.DatabaseError):
            MacVendorsClient(bad)

    def test_missing_mac_addresses_table_raises(self, tmp_path: Path) -> None:
        db_path = tmp_path / "empty.db"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE something_else (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        with pytest.raises(sqlite3.DatabaseError, match="no mac_addresses table"):
            MacVendorsClient(db_path)

    def test_incomplete_mac_addresses_table_raises(self, tmp_path: Path) -> None:
        db_path = tmp_path / "partial.db"
        conn = sqlite3.connect(db_path)
        # A table of the right name but without the range columns: the lookup
        # could never work, so it must fail at open, not on the first query.
        conn.execute(
            "CREATE TABLE mac_addresses (id INTEGER PRIMARY KEY, assignment TEXT, "
            "organization_name TEXT)"
        )
        conn.commit()
        conn.close()

        with pytest.raises(sqlite3.DatabaseError, match="range_begin, range_end, bits"):
            MacVendorsClient(db_path)

    def test_path_with_special_chars(self, tmp_path: Path) -> None:
        # A path containing a space and '#' must be opened correctly (URI is
        # percent-encoded), not misparsed into a different/empty database.
        sub = tmp_path / "my data"
        sub.mkdir()
        db_path = make_export_db(sub / "v#1.db")
        with MacVendorsClient(db_path) as client:
            match = client.lookup("00:50:56:AA:BB:CC")
        assert match is not None
        assert match.organization_name == "VMware, Inc."

    def test_read_only(self, export_db: Path) -> None:
        # The connection is opened read-only; writes must fail.
        with MacVendorsClient(export_db) as client:
            with pytest.raises(sqlite3.OperationalError):
                client._conn.execute("DELETE FROM mac_addresses")
