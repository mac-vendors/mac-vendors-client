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
it opens the file:

| Export | Extra columns | What you get |
|---|---|---|
| Free monthly snapshot (`minimal`) | none, and no `organization_address` | `organization_address` is `""` |
| Client feed (`short_names`) | `short_name` | `short_name` / `display_name` |
| Full export (`enriched`) | `short_name` and vendor enrichment | as above; enrichment columns are ignored |

Only `assignment`, `organization_name`, `range_begin`, `range_end` and `bits`
are required. A database without them is rejected when the client opens it, not
on the first lookup.

Use a **current** export. A history (`history` / `as_of`) export keeps several
rows per assignment, and this client has no temporal filter, so a lookup there
can resolve to a superseded row.

## License

MIT
