# mac-vendors-client

[![PyPI](https://img.shields.io/pypi/v/mac-vendors-client.svg)](https://pypi.org/project/mac-vendors-client/)
[![Python versions](https://img.shields.io/pypi/pyversions/mac-vendors-client.svg)](https://pypi.org/project/mac-vendors-client/)
[![CI](https://github.com/mac-vendors/mac-vendors-client/actions/workflows/ci.yml/badge.svg)](https://github.com/mac-vendors/mac-vendors-client/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Lightweight, **offline** MAC address -> vendor lookups against a pre-built
SQLite database export.

- **Zero dependencies** - reads the export via the standard library `sqlite3`.
- **Fast** - range/bits index lookup, no network, no per-call cost.
- **Read-only** - opens the export file read-only; never modifies it.

Use it when you have a downloaded SQLite export and want fast, local lookups
with no network calls. For live queries against the hosted API, use the online
SDK ([`mac-vendors-sdk`](https://pypi.org/project/mac-vendors-sdk/)) instead.

## Install

```bash
pip install mac-vendors-client
```

## Usage

```python
from mac_vendors_client import MacVendorsClient

with MacVendorsClient("vendors.db") as client:
    match = client.lookup("00:50:56:AA:BB:CC")
    if match:
        print(match.organization_name)  # "VMware, Inc."
        print(match.assignment, match.bits)

    # Full organization name only
    name = client.lookup_name("00-50-56-aa-bb-cc")

    # Short brand name when the export carries one, full name otherwise
    label = match.display_name if match else None

    # Batch (invalid entries map to None instead of raising)
    results = client.lookup_batch(["0050.56AA.BBCC", "FF:FF:FF:00:00:00"])

    # Export metadata
    info = client.info()
    print(info.exported_at, info.total_records)
```

MAC input accepts any common format (`:`/`-`/`.` separators or bare hex), and
a shorter prefix such as a 6-hex OUI. The most specific (largest-`bits`) match
wins when prefixes overlap (e.g. an MA-S assignment inside an MA-L block).

### Point-in-time lookups

A **history** export keeps every version of an assignment. Against one of
those, a plain lookup answers with the current version, and `as_of` asks what
the registry said at an instant:

```python
from datetime import date
from mac_vendors_client import MacVendorsClient, end_of_day

with MacVendorsClient("mac_vendors_history.db") as client:
    assert client.has_history  # False for every other mode

    client.lookup_name("00:50:56:AA:BB:CC")  # the current vendor
    client.lookup_name("005056", as_of="2006-08-20T00:00:00Z")
    client.lookup_name("005056", as_of=end_of_day(date(2006, 8, 20)))
```

`as_of` takes a `datetime` or a string spelled the way the export's timestamp
columns are (`YYYY-MM-DDTHH:MM:SSZ`); a `datetime` is spelled for you.
`datetime.isoformat()` is **not** that spelling - it writes `+00:00` and a
fractional part, both of which sort below `Z` and would quietly answer with the
previous version. For a whole calendar day use `end_of_day(day)`: a bare date
sorts below every timestamp on it, so it would export the day before. Passing a
`date` raises rather than picking one of its two readings, and `as_of` against
an export that keeps no versions raises too.

## The export contract

The export is a SQLite database with this schema:

```sql
CREATE TABLE mac_addresses (
    id INTEGER PRIMARY KEY,
    assignment TEXT NOT NULL,
    organization_name TEXT,
    organization_address TEXT,      -- absent from the minimal snapshot
    range_begin INTEGER NOT NULL,   -- 48-bit MAC range start
    range_end   INTEGER NOT NULL,   -- 48-bit MAC range end
    bits INTEGER NOT NULL,          -- 24 / 28 / 36
    short_name TEXT                 -- only in the client feed and full export
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);  -- updated_at, exported_at, total_records
```

A lookup resolves a MAC `M` to the row where
`range_begin <= int(M) <= range_end`, ordered by `bits DESC` (most specific
first).

Exports come in several column shapes and the client reads the shape once when
it opens the file. Every mode resolves a MAC; what differs is how much each
match carries:

| Export | Extra columns | What you get |
|---|---|---|
| Free monthly snapshot (`minimal`) | none, and no `organization_address` | `organization_address` is `""` |
| Client feed (`short_names`) | `short_name` | `short_name` / `display_name` |
| Full export (`enriched`) | `short_name`, `country_code`, `assignment_count`, `registries`, `first_seen`, `last_seen` | all of them on the match |
| History dump (`history`) | `valid_from`, `valid_to` | the current version, or any past one via `as_of` |
| Point-in-time (`as_of`) | none | one row per assignment, as of that instant |

Only `assignment`, `organization_name`, `range_begin`, `range_end` and `bits`
are required. A database without them is rejected when the client opens it, not
on the first lookup. A column the export does not carry reads as `""` (or `0`),
so the same code works against every mode.

## License

MIT
