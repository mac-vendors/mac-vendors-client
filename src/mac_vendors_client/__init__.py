"""mac-vendors-client - offline MAC -> vendor lookups against an exported SQLite DB.

Example:
    from mac_vendors_client import MacVendorsClient

    with MacVendorsClient("vendors.db") as client:
        match = client.lookup("00:50:56:AA:BB:CC")
        if match:
            print(match.organization_name)

The export file is a SQLite database in the MAC Vendors offline export format.
This package has no third-party dependencies; it reads via the stdlib sqlite3.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from .client import MacVendorsClient
from .models import ExportInfo, VendorMatch
from .temporal import end_of_day, stored_timestamp

try:
    __version__ = _version("mac-vendors-client")
except PackageNotFoundError:  # pragma: no cover - not installed (source tree)
    __version__ = "0.0.0"

__all__ = [
    "ExportInfo",
    "MacVendorsClient",
    "VendorMatch",
    "__version__",
    "end_of_day",
    "stored_timestamp",
]
