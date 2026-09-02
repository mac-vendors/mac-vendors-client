"""Data structures returned by the client."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VendorMatch:
    """A single MAC -> vendor lookup result.

    Attributes:
        assignment: The matching IEEE assignment prefix (hex), e.g. "005056".
        organization_name: Vendor / organization name.
        organization_address: Vendor address. Empty when the export omits the
            column (the free "minimal" snapshot does).
        bits: Prefix length in bits (24 = MA-L/CID, 28 = MA-M, 36 = MA-S/IAB).
            The most specific (largest ``bits``) match is returned.
        short_name: Short brand name ("Cisco" for "Cisco Systems, Inc"), from
            exports that carry the column. Empty otherwise - use
            ``display_name`` rather than reading this directly.

        The last five are vendor-level enrichment, carried only by the full
        ("enriched") export and blank in every other one. They describe the
        organization rather than this assignment:

        country_code: ISO 3166-1 alpha-2 country of the organization.
        assignment_count: How many assignments the organization holds.
        registries: The registries it appears in, comma-separated
            ("MA-L,MA-S").
        first_seen: When the organization was first recorded (ISO 8601).
        last_seen: When its record last changed (ISO 8601).
    """

    assignment: str
    organization_name: str
    organization_address: str
    bits: int
    short_name: str = ""
    country_code: str = ""
    assignment_count: int = 0
    registries: str = ""
    first_seen: str = ""
    last_seen: str = ""

    @property
    def display_name(self) -> str:
        """The short brand name if the export has one, else the full name."""
        return self.short_name or self.organization_name


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
