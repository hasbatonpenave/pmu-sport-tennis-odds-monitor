"""
feed/stream.py — Kambi Socket.IO push client

Replaces REST polling with a persistent Socket.IO connection.
Subscribes/unsubscribes per-event as the match list changes.
Pushes OddsUpdate onto the shared queue (same interface as before).
"""
import asyncio
import json
import logging
import time

import socketio

from api.models import OddsUpdate
from config import settings
from feed.parser import diff_odds

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


class KambiPushClient:
    """Persistent Socket.IO connection to Kambi push feed.

    Usage:
        client = KambiPushClient(queue, stop_ev, meta_getter)
        await client.run()          # blocks until stop_ev set

    meta_getter: callable(event_id: int) -> MatchMeta | None
    """

    def __init__(
        self,
        queue: asyncio.Queue,
        stop_ev: asyncio.Event,
        meta_getter,
    ):
        self._queue = queue
        self._stop_ev = stop_ev
        self._meta_getter = meta_getter
        self._sio = socketio.AsyncClient(logger=False, engineio_logger=False, ssl_verify=False)
        self._subscribed: set[int] = set()
        self._prev_odds: dict[tuple[int, str], dict[str, float]] = {}
        self._breaker = CircuitBreaker()
        self._connected = asyncio.Event()

        self._sio.on("connect", self._on_connect)
        self._sio.on("disconnect", self._on_disconnect)
        self._sio.on("message", self._on_message)

    async def subscribe(self, event_id: int) -> None:
        """Subscribe to a tennis event. Safe to call before connected."""
        self._subscribed.add(event_id)
        if self._sio.connected:
            topic = f"{settings.kambi_topic_prefix}.{event_id}.json"
            await self._sio.emit("subscribe", {"topic": topic})
            logger.debug("Subscribed to event %d", event_id)

    async def unsubscribe(self, event_id: int) -> None:
        """Unsubscribe from a finished event."""
        self._subscribed.discard(event_id)
        if self._sio.connected:
            topic = f"{settings.kambi_topic_prefix}.{event_id}.json"
            await self._sio.emit("unsubscribe", {"topic": topic})

    async def run(self) -> None:
        """Main loop: connect, resubscribe on reconnect, reconnect on error."""
        while not self._stop_ev.is_set():
            if self._breaker.is_open:
                remaining = self._breaker._open_until - time.monotonic()
                try:
                    await asyncio.wait_for(self._stop_ev.wait(), timeout=max(remaining, 0))
                except asyncio.TimeoutError:
                    pass
                continue

            try:
                logger.info("Connecting to Kambi push feed...")
                await self._sio.connect(
                    settings.kambi_push_url,
                    headers={"Origin": "https://www.pmu.fr"},
                    transports=["websocket"],
                    wait_timeout=15,
                )
                self._connected.set()
                logger.info("Connected to Kambi push feed")
                self._breaker.record_success()

                while self._sio.connected and not self._stop_ev.is_set():
                    await asyncio.sleep(1)

            except Exception as exc:
                delay = self._breaker.next_delay()
                logger.error("Push feed connection failed (delay=%.1fs): %s", delay, exc)
                self._connected.clear()
                try:
                    await asyncio.wait_for(self._stop_ev.wait(), timeout=min(delay, 10.0))
                except asyncio.TimeoutError:
                    pass

        if self._sio.connected:
            await self._sio.disconnect()

    async def _on_connect(self):
        """Re-subscribe to all known events after reconnect."""
        self._connected.set()
        for event_id in list(self._subscribed):
            topic = f"{settings.kambi_topic_prefix}.{event_id}.json"
            await self._sio.emit("subscribe", {"topic": topic})
        logger.info("Resubscribed to %d events", len(self._subscribed))

    async def _on_disconnect(self):
        self._connected.clear()
        logger.warning("Disconnected from Kambi push feed")

    async def _on_message(self, data):
        """Parse incoming Socket.IO message and push OddsUpdate to queue."""
        try:
            frames = json.loads(data) if isinstance(data, str) else data
        except Exception:
            return

        if not isinstance(frames, list):
            return

        for frame in frames:
            if not isinstance(frame, dict):
                continue

            mt = frame.get("mt")

            if mt == 6:
                await self._handle_boa(frame)
            elif mt == 8:
                await self._handle_bosu(frame)
            # mt=22 (booa) ignored — outcome-level status changes, no odds

    async def _handle_boa(self, frame: dict) -> None:
        """Parse mt=6 Bet Offer Amendment frame.

        Field path: boa.betOffer -> eventId, criterion, outcomes[] with odds (×1000).
        """
        bo = frame.get("boa", {}).get("betOffer", {})
        event_id = bo.get("eventId")
        if not event_id:
            return

        # Check if market-level suspended
        if bo.get("suspended"):
            return

        criterion = bo.get("criterion", {})
        label = criterion.get("englishLabel", "")

        # Filter to tracked tennis markets
        if not any(t in label for t in settings.tracked_markets):
            return

        # Build market key — include line for O/U and handicap
        market_key = label
        outcomes = bo.get("outcomes", [])
        for outcome in outcomes:
            line = outcome.get("line")
            if line is not None:
                market_key = f"{label} {line / 1000.0:g}"
                break

        odds: dict[str, float] = {}
        for outcome in outcomes:
            if outcome.get("status") != "OPEN":
                continue
            odd_int = outcome.get("odds")
            if odd_int is None:
                continue
            sel_label = outcome.get("englishLabel") or outcome.get("label", "")
            odds[sel_label] = odd_int / 1000.0

        if not odds:
            return

        self._push_update(event_id, market_key, odds)

    async def _handle_bosu(self, frame: dict) -> None:
        """Parse mt=8 Bet Offer Suspension frame.

        Lightweight suspension toggle — no odds data.
        """
        bosu = frame.get("bosu", {})
        event_id = bosu.get("eventId")
        if event_id and bosu.get("suspended"):
            update = OddsUpdate(
                match_id=event_id,
                market="__suspended__",
                odds={},
                live=True,
            )
            try:
                self._queue.put_nowait(update)
            except asyncio.QueueFull:
                pass

    def _push_update(self, event_id: int, market: str, odds: dict) -> None:
        """Diff against previous and push only if changed."""
        key = (event_id, market)
        prev = self._prev_odds.get(key, {})
        changed, movements = diff_odds(event_id, market, odds, prev)
        if not changed:
            return

        self._prev_odds[key] = odds
        meta = self._meta_getter(event_id)

        update = OddsUpdate(
            match_id=event_id,
            market=market,
            odds=changed,
            movements=movements,
            meta=meta,
            live=True,
        )
        try:
            self._queue.put_nowait(update)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(update)
            except asyncio.QueueEmpty:
                pass
