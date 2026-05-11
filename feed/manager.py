import asyncio
import logging
import time

from api.client import KambiClient
from api.models import MatchMeta, OddsUpdate
from config import SPORT_MARKETS, settings
from feed.parser import parse_match_list, parse_betoffers
from feed.stream import KambiPushClient

logger = logging.getLogger(__name__)


class FeedManager:
    """Orchestrates match discovery and live odds polling.

    - Refreshes match list every match_refresh_min (default 5 min)
    - Spawns a background live odds polling task
    - Optionally fetches deep markets for new matches via betoffer endpoint
    """

    def __init__(self, client: KambiClient, queue: asyncio.Queue):
        self._client = client
        self._queue = queue
        self._stop_ev = asyncio.Event()
        self._meta: dict[int, MatchMeta] = {}
        self._live_task: asyncio.Task | None = None
        self._push_client: KambiPushClient | None = None
        self._updates_pushed = 0

    @property
    def meta(self) -> dict[int, MatchMeta]:
        return self._meta

    @property
    def updates_pushed(self) -> int:
        return self._updates_pushed

    async def run(self) -> None:
        """Main feed loop: refresh match list, keep live poll running."""
        self._push_client = KambiPushClient(
            queue=self._queue,
            stop_ev=self._stop_ev,
            meta_getter=lambda eid: self._meta.get(eid),
        )
        self._live_task = asyncio.create_task(self._push_client.run())

        while not self._stop_ev.is_set():
            try:
                await self._refresh_matches()
            except Exception as exc:
                logger.error("Match list refresh failed: %s", exc)

            try:
                await asyncio.wait_for(
                    self._stop_ev.wait(),
                    timeout=settings.match_refresh_min * 60,
                )
            except asyncio.TimeoutError:
                pass

    async def _refresh_matches(self) -> None:
        total_matches = 0
        total_live = 0

        for sport in settings.sports:
            try:
                data = await self._client.list_matches(sport_name=sport)
                tracked = SPORT_MARKETS.get(sport, settings.tracked_markets)
                meta_by_id, prices_by_id = parse_match_list(
                    data, sport_name=sport, tracked_markets=tracked,
                )
            except Exception as exc:
                logger.error("Match list refresh failed for %s: %s", sport, exc)
                continue

            # Update meta cache
            self._meta.update(meta_by_id)

            # Subscribe to new event IDs on push feed
            for match_id in meta_by_id:
                if self._push_client and match_id not in self._push_client._subscribed:
                    try:
                        await self._push_client.subscribe(match_id)
                    except Exception as exc:
                        logger.debug("Subscribe failed for %d: %s", match_id, exc)

            # Push initial odds snapshots for new/changed matches
            for match_id, market_odds in prices_by_id.items():
                meta = meta_by_id.get(match_id)
                if meta is None:
                    continue

                for market, odds in market_odds.items():
                    update = OddsUpdate(
                        match_id=match_id,
                        market=market,
                        odds=odds,
                        meta=meta,
                        live=meta.state == "STARTED",
                    )
                    try:
                        self._queue.put_nowait(update)
                        self._updates_pushed += 1
                    except asyncio.QueueFull:
                        try:
                            self._queue.get_nowait()
                            self._queue.put_nowait(update)
                            self._updates_pushed += 1
                        except asyncio.QueueEmpty:
                            pass

            sport_live = sum(1 for m in meta_by_id.values() if m.state == "STARTED")
            total_matches += len(meta_by_id)
            total_live += sport_live
            logger.info(
                "%s refresh: %d matches (%d live)",
                sport, len(meta_by_id), sport_live,
            )

        logger.info(
            "Match refresh done: %d matches total (%d live), %d updates pushed",
            total_matches, total_live, self._updates_pushed,
        )

    async def fetch_deep_markets(self, match_id: int, max_markets: int = 10) -> None:
        """Fetch detailed bet offers for a single match (throttled, best-effort)."""
        try:
            data = await self._client.get_betoffers(match_id)
            meta = self._meta.get(match_id)
            sport = meta.sport if meta else "TENNIS"
            tracked = SPORT_MARKETS.get(sport, settings.tracked_markets)
            updates = parse_betoffers(data, match_id, tracked_markets=tracked)
            for update in updates[:max_markets]:
                update.meta = self._meta.get(match_id)
                update.live = update.meta.state == "STARTED" if update.meta else False
                try:
                    self._queue.put_nowait(update)
                    self._updates_pushed += 1
                except asyncio.QueueFull:
                    break
        except Exception as exc:
            logger.debug("Deep market fetch failed for %d: %s", match_id, exc)

    def stop(self) -> None:
        self._stop_ev.set()

    async def shutdown(self) -> None:
        self.stop()
        if self._live_task:
            self._live_task.cancel()
            try:
                await self._live_task
            except asyncio.CancelledError:
                pass
