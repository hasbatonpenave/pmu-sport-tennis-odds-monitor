from abc import ABC, abstractmethod

from api.models import OddsUpdate, PricePoint


class PriceRepository(ABC):
    """Abstract interface for price storage."""

    @abstractmethod
    def enqueue(self, update: OddsUpdate) -> None:
        """Non-blocking enqueue of an odds update for persistence."""
        ...

    @abstractmethod
    def get_history(
        self,
        match_id: int,
        selection: str,
        market: str,
        limit: int = 500,
    ) -> list[PricePoint]:
        """Synchronous fetch of price history (oldest first)."""
        ...

    @abstractmethod
    def get_markets(self, match_id: int) -> list[dict]:
        """Return distinct {market, selection} pairs stored for a match."""
        ...

    @abstractmethod
    def start(self) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def join(self, timeout: float = 10.0) -> None:
        ...
