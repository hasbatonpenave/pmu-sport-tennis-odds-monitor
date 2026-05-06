import asyncio
import logging
import time
from collections import defaultdict
from typing import Optional

from api.client import KambiClient
from api.models import OddsUpdate
from config import settings
from feed.parser import diff_odds, parse_live_events

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Per-stream circuit breaker with exponential backoff.

    - CLOSED: normal operation, failures tracked, exponential backoff on retry
    - OPEN: after cb_max_failures consecutive failures, park for cb_reset_after_s
    """

    def __init__(self):
        self._failures = 0
        self._open_until: float = 0.0

    @property
    def is_open(self) -> bool:
        if self._open_until == 0:
            return False
        if time.monotonic() >= self._open_until:
            self._failures = 0
            self._open_until = 0.0
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0

    def next_delay(self) -> float:
        self._failures += 1
        if self._failures >= settings.cb_max_failures:
            self._open_until = time.monotonic() + settings.cb_reset_after_s
            logger.warning("Circuit breaker OPEN for %.0fs", settings.cb_reset_after_s)
            return settings.cb_reset_after_s
        delay = min(settings.reconnect_delay_s * (2 ** (self._failures - 1)), 60.0)
        return delay


async def poll_live_odds(
    client: KambiClient,
    queue: asyncio.Queue,
    stop_ev: asyncio.Event,
) -> None:
    """Poll live/open.json every poll_interval_s, diff against previous, push changes.

    This is the primary live odds feed. It polls the lightweight live endpoint
    (which returns only mainBetOffer + liveData for active events) and pushes
    only changed outcomes to the shared queue.
    """
    breaker = CircuitBreaker()
    # Track last-seen odds per (match_id, market) for diffing
    prev_odds: dict[tuple[int, str], dict[str, float]] = {}
    # Track version timestamps for early skip
    prev_versions: dict[int, int] = {}
    ncid = 0

    while not stop_ev.is_set():
        if breaker.is_open:
            remaining = breaker._open_until - time.monotonic()
            if remaining > 0:
                logger.info("Circuit open, sleeping %.1fs", remaining)
                try:
                    await asyncio.wait_for(stop_ev.wait(), timeout=min(remaining, 10.0))
                except asyncio.TimeoutError:
                    pass
                continue

        try:
            data = await client.get_live_events(ncid=ncid)
            ncid = int(time.time() * 1000)

            updates = parse_live_events(data)

            for update in updates:
                key = (update.match_id, update.market)
                prev = prev_odds.get(key, {})

                # Check if anything changed
                changed, movements = diff_odds(update.match_id, update.market, update.odds, prev)
                if not changed:
                    continue

                prev_odds[key] = update.odds
                update.movements = movements

                # Push to shared queue (drop oldest if full)
                try:
                    queue.put_nowait(update)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                        queue.put_nowait(update)
                    except asyncio.QueueEmpty:
                        pass

            breaker.record_success()

        except Exception as exc:
            delay = breaker.next_delay()
            logger.error("Live poll error (failures=%d, delay=%.1fs): %s", breaker._failures, delay, exc)
            try:
                await asyncio.wait_for(stop_ev.wait(), timeout=min(delay, 10.0))
            except asyncio.TimeoutError:
                pass

        # Wait for next poll interval (interruptible by stop)
        try:
            await asyncio.wait_for(stop_ev.wait(), timeout=settings.poll_interval_s)
        except asyncio.TimeoutError:
            pass
