"""Tests for MacVendorsClient and MAC normalization."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import CHANGED_AT, make_export_db

from mac_vendors_client import (
    ExportInfo,
    MacVendorsClient,
    VendorMatch,
    end_of_day,
    stored_timestamp,
)
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

    def test_enriched_export_carries_vendor_columns(self, enriched_export_db: Path) -> None:
        with MacVendorsClient(enriched_export_db) as client:
            match = client.lookup("00:50:56:AA:BB:CC")
            unmatched = client.lookup("00:11:22:30:00:00")
        assert match is not None
        assert match.country_code == "US"
        assert match.assignment_count == 3
        assert match.registries == "MA-L,MA-S"
        assert match.first_seen == "2004-06-11T00:00:00Z"
        assert match.last_seen == "2026-01-01T00:00:00Z"
        # A record with no vendors row to join reads blank, not None.
        assert unmatched is not None
        assert unmatched.country_code == ""
        assert unmatched.assignment_count == 0

    def test_export_without_enrichment_reads_blank(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            match = client.lookup("00:50:56:AA:BB:CC")
        assert match is not None
        assert match.country_code == ""
        assert match.assignment_count == 0
        assert match.registries == ""
        assert match.first_seen == ""
        assert match.last_seen == ""


class TestHistoryExport:
    """A history export holds every version, so a lookup has to pick one."""

    def test_current_version_wins(self, history_export_db: Path) -> None:
        # Both versions of 005056 cover this address with the same bits and the
        # same range_begin, so only the current-row filter separates them.
        with MacVendorsClient(history_export_db) as client:
            match = client.lookup("00:50:56:AA:BB:CC")
        assert match is not None
        assert match.organization_name == "VMware, Inc."

    def test_as_of_returns_the_version_of_that_day(self, history_export_db: Path) -> None:
        with MacVendorsClient(history_export_db) as client:
            before = client.lookup("00:50:56:AA:BB:CC", as_of="2005-01-01T00:00:00Z")
            after = client.lookup("00:50:56:AA:BB:CC", as_of="2020-01-01T00:00:00Z")
        assert before is not None
        assert before.organization_name == "VMWARE INC"
        assert after is not None
        assert after.organization_name == "VMware, Inc."

    def test_as_of_is_half_open_at_the_change(self, history_export_db: Path) -> None:
        # The instant of the change belongs to the version that starts there.
        with MacVendorsClient(history_export_db) as client:
            assert client.lookup_name("005056", as_of=CHANGED_AT) == "VMware, Inc."
            assert client.lookup_name("005056", as_of="2010-05-31T23:59:59Z") == "VMWARE INC"

    def test_as_of_before_the_first_version_is_unknown(self, history_export_db: Path) -> None:
        with MacVendorsClient(history_export_db) as client:
            assert client.lookup("00:50:56:AA:BB:CC", as_of="1999-01-01T00:00:00Z") is None

    def test_as_of_takes_a_datetime(self, history_export_db: Path) -> None:
        # isoformat() would write "+00:00" and a fraction where the column
        # writes "Z"; both sort below it and would answer with the old version.
        moment = datetime(2020, 1, 1, 12, 30, 45, 500000, tzinfo=UTC)
        with MacVendorsClient(history_export_db) as client:
            assert client.lookup_name("005056", as_of=moment) == "VMware, Inc."
            # Same instant, spelled in another zone: still the same answer.
            elsewhere = moment.astimezone(timezone(timedelta(hours=5)))
            assert client.lookup_name("005056", as_of=elsewhere) == "VMware, Inc."

    def test_as_of_takes_a_naive_datetime_as_utc(self, history_export_db: Path) -> None:
        with MacVendorsClient(history_export_db) as client:
            assert client.lookup_name("005056", as_of=datetime(2005, 1, 1)) == "VMWARE INC"

    def test_as_of_rejects_a_date(self, history_export_db: Path) -> None:
        # A date has two readings; end_of_day names the one a lookup wants.
        with MacVendorsClient(history_export_db) as client:
            with pytest.raises(TypeError, match="end_of_day"):
                client.lookup("005056", as_of=date(2020, 1, 1))  # type: ignore[arg-type]
            assert client.lookup_name("005056", as_of=end_of_day(date(2005, 1, 1))) == "VMWARE INC"

    def test_as_of_rejects_a_number(self, history_export_db: Path) -> None:
        with MacVendorsClient(history_export_db) as client:
            with pytest.raises(TypeError, match="str or datetime"):
                client.lookup("005056", as_of=1234567890)  # type: ignore[arg-type]

    def test_batch_as_of(self, history_export_db: Path) -> None:
        with MacVendorsClient(history_export_db) as client:
            results = client.lookup_batch(
                ["005056", "001122", "bad-zz"], as_of="2005-01-01T00:00:00Z"
            )
        assert results["005056"] is not None
        assert results["005056"].organization_name == "VMWARE INC"
        assert results["001122"] is not None
        assert results["bad-zz"] is None

    def test_has_history(self, history_export_db: Path, export_db: Path) -> None:
        with MacVendorsClient(history_export_db) as client:
            assert client.has_history is True
        with MacVendorsClient(export_db) as client:
            assert client.has_history is False

    def test_as_of_needs_a_history_export(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            with pytest.raises(ValueError, match="history export"):
                client.lookup("005056", as_of="2005-01-01T00:00:00Z")

    def test_batch_as_of_needs_a_history_export(self, export_db: Path) -> None:
        # The per-address guard that maps a malformed MAC to None must not
        # swallow this into a batch of Nones.
        with MacVendorsClient(export_db) as client:
            with pytest.raises(ValueError, match="history export"):
                client.lookup_batch(["005056"], as_of="2005-01-01T00:00:00Z")


class TestTemporalSpelling:
    def test_stored_timestamp_drops_the_fraction_and_writes_z(self) -> None:
        moment = datetime(2020, 1, 2, 3, 4, 5, 999999, tzinfo=UTC)
        assert stored_timestamp(moment) == "2020-01-02T03:04:05Z"

    def test_stored_timestamp_converts_to_utc(self) -> None:
        moment = datetime(2020, 1, 2, 8, 4, 5, tzinfo=timezone(timedelta(hours=5)))
        assert stored_timestamp(moment) == "2020-01-02T03:04:05Z"

    def test_end_of_day_is_the_last_instant_of_the_day(self) -> None:
        # A bare date sorts below every timestamp on it and would answer with
        # the previous day.
        assert end_of_day(date(2020, 1, 2)) == "2020-01-02T23:59:59Z"


class TestBatch:
    def test_batch_mixed(self, export_db: Path) -> None:
        with MacVendorsClient(export_db) as client:
            results = client.lookup_batch(["00:50:56:AA:BB:CC", "FF:FF:FF:00:00:00", "bad-zz"])
        assert results["00:50:56:AA:BB:CC"] is not None
        assert results["00:50:56:AA:BB:CC"].organization_name == "VMware, Inc."
        assert results["FF:FF:FF:00:00:00"] is None
        assert results["bad-zz"] is None  # invalid -> None, does not abort batch

    def test_batch_does_not_swallow_a_database_error(self, export_db: Path) -> None:
        # The per-address guard covers the MAC parse and nothing else: a failure
        # from the query itself must propagate, not be reported as "unknown".
        client = MacVendorsClient(export_db)
        client.close()
        with pytest.raises(sqlite3.ProgrammingError):
            client.lookup_batch(["005056"])

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
