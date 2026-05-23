"""Data structures returned by the client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VendorMatch:
    """A single MAC -> vendor lookup result.

    Attributes:
        assignment: The matching IEEE assignment prefix (hex), e.g. "005056".
        organization_name: Vendor / organization name.
        organization_address: Vendor address (may be empty).
        bits: Prefix length in bits (24 = MA-L/CID, 28 = MA-M, 36 = MA-S/IAB).
            The most specific (largest ``bits``) match is returned.
    """

    assignment: str
    organization_name: str
    organization_address: str
    bits: int


@dataclass(frozen=True, slots=True)
class ExportInfo:
    """Metadata about the exported database, read from its ``metadata`` table.

    Attributes:
        updated_at: When the source data was last updated (ISO 8601), if known.
        exported_at: When the export file was generated (ISO 8601), if known.
        total_records: Number of vendor records in the export.
    """

    updated_at: str | None
    exported_at: str | None
    total_records: int
