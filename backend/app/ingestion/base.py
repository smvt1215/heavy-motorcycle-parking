"""City adapter interface: envelope validation, timestamps and per-record normalization.

Adapters are pure: no I/O and no database identifiers. The pipeline owns raw
record retention and records each `RecordError` against the offending raw record.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from app.ingestion.contracts import NormalizedLot, NormalizedRealtime


class BaseParkingAdapter(ABC):
    """Source-specific parsing; city formats never leak into domain logic."""

    city: str

    @abstractmethod
    def records(self, payload: Any) -> list[Any]:
        """Return the raw record list; raise `RecordError` for envelope problems."""

    @abstractmethod
    def source_updated_at(self, payload: Any) -> datetime | None:
        """Timezone-aware source update instant; None when missing or unparseable."""

    @abstractmethod
    def normalize_static(self, record: Any) -> NormalizedLot:
        """Normalize one static record; raise `RecordError` for a record failure."""

    @abstractmethod
    def normalize_realtime(self, record: Any) -> NormalizedRealtime:
        """Normalize one realtime record; raise `RecordError` for a record failure."""

    def record_key(self, record: Any) -> str | None:
        """Best-effort source identifier for raw-record bookkeeping; never raises."""
        return None
