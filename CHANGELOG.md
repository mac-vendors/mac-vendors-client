# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-09-02

### Fixed
- A lookup against a **history** export no longer resolves to a superseded
  row. That mode keeps every version of an assignment, and the versions share a
  range, `bits` and `range_begin`, so the most-specific-wins ordering could not
  separate them and the answer was whichever row SQLite reached first - in
  practice the oldest. The lookup now filters to the current version
  (`valid_to IS NULL`) when the export carries the temporal columns. Nothing
  changes for the other modes: they hold one row per assignment and have no
  such column.

### Added
- `lookup(mac, as_of=...)`, and the same keyword-only argument on `lookup_name`
  and `lookup_batch` (spelled as in `mac-vendors-db`'s own `lookup`): against a
  history export, resolve the address as the registry stood at an instant.
  Takes a `datetime` or a string spelled the way the export's timestamp columns
  are; `MacVendorsClient.has_history` says whether an export can answer it, and
  it raises for one that cannot - before the first address of a batch, not once
  per address.
- `end_of_day(day)` and `stored_timestamp(moment)`. The temporal columns are
  text compared as text, so an `as_of` bound has to be spelled the way they
  are: `datetime.isoformat()` writes `+00:00` and a fractional part, and both
  sort below `Z`, so a bound spelled that way lands on the previous version
  without erroring. A `date` is refused rather than guessed at - it means
  either end of its day, and `end_of_day` names the one a lookup wants.
- `VendorMatch` carries the full export's vendor-level enrichment:
  `country_code`, `assignment_count`, `registries`, `first_seen` and
  `last_seen`. They were read from the file and dropped before. Blank in every
  export that does not carry the columns, as `short_name` already was.

## [1.1.0] - 2026-08-30

### Fixed
- Reading the free monthly snapshot no longer fails. That export is built in
  `minimal` mode, which has no `organization_address` column, so every lookup
  raised `sqlite3.OperationalError: no such column: organization_address`. The
  client now reads the table's columns when it opens the file and selects only
  what is there; a missing `organization_address` reads as `""`.

### Added
- `VendorMatch.short_name` and `VendorMatch.display_name`. The licensed client
  feed and the full export carry a short brand name ("Cisco" for "Cisco
  Systems, Inc"); `display_name` is that name when the export has one and
  `organization_name` otherwise.

### Changed
- A database whose `mac_addresses` table is missing or lacks a required column
  now raises `sqlite3.DatabaseError` when the client opens it, instead of
  failing on the first lookup.

## [1.0.1] - 2026-08-30

### Fixed
- Reject malformed MAC input that `int(value, 16)` used to accept (a leading
  sign, an `0x` prefix, underscore separators, non-ASCII digits). Those inputs
  were counted in the address length and silently resolved to the wrong vendor;
  they now raise `ValueError` like any other invalid MAC.

### Changed
- `lookup_batch()` runs one query per distinct input string, so a batch that
  repeats the same address no longer pays for it twice.

## [1.0.0] - 2026-07-23

### Added
- Initial public release: offline MAC address to vendor lookup against a
  pre-built SQLite export, with zero runtime dependencies and a fully typed API.

[1.2.0]: https://github.com/mac-vendors/mac-vendors-client/releases/tag/v1.2.0
[1.1.0]: https://github.com/mac-vendors/mac-vendors-client/releases/tag/v1.1.0
[1.0.1]: https://github.com/mac-vendors/mac-vendors-client/releases/tag/v1.0.1
[1.0.0]: https://github.com/mac-vendors/mac-vendors-client/releases/tag/v1.0.0
