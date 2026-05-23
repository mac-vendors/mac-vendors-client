# mac-vendors-client

Lightweight, **offline** MAC address -> vendor lookups against a pre-built
SQLite database exported by [`mac-vendors-db`](../mac-vendors-db).

- **Zero dependencies** - reads the export via the standard library `sqlite3`.
- **Fast** - range/bits index lookup, no network, no per-call cost.
- **Read-only** - opens the export file read-only; never modifies it.

This is the consumer-side companion to `mac-vendors-db`. Use it when you have
a downloaded export file and want local lookups. For live queries against the
hosted API, use the online SDK instead.

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
        print(match.organization_name)   # "VMware, Inc."
        print(match.assignment, match.bits)

    # Name only
    name = client.lookup_name("00-50-56-aa-bb-cc")

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

The database is the SQLite artifact produced by `mac-vendors-db`'s exporter:

```sql
CREATE TABLE mac_addresses (
    id INTEGER PRIMARY KEY,
    assignment TEXT NOT NULL,
    organization_name TEXT,
    organization_address TEXT,
    range_begin INTEGER NOT NULL,   -- 48-bit MAC range start
    range_end   INTEGER NOT NULL,   -- 48-bit MAC range end
    bits INTEGER NOT NULL           -- 24 / 28 / 36
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);  -- updated_at, exported_at, total_records
```

A lookup resolves a MAC `M` to the row where
`range_begin <= int(M) <= range_end`, ordered by `bits DESC` (most specific
first).

## License

MIT
