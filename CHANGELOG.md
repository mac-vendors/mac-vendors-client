# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.1.0]: https://github.com/mac-vendors/mac-vendors-client/releases/tag/v1.1.0
[1.0.1]: https://github.com/mac-vendors/mac-vendors-client/releases/tag/v1.0.1
[1.0.0]: https://github.com/mac-vendors/mac-vendors-client/releases/tag/v1.0.0
