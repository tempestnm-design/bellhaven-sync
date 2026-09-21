"""Interface implemented by operator website adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Facility


class FacilitySource(ABC):
    @abstractmethod
    def fetch_facilities(self) -> list[Facility]:
        """Return a complete validated facility snapshot or raise."""
