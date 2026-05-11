import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from api.client import KambiClient, make_session
from api.models import OddsUpdate
from config import settings
from feed.manager import FeedManager
from storage.repository import PriceRepository
from storage.sqlite import SQLiteRepository

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── Global state (attached to app.state in lifespan) ───────────

_subscribers: list[asyncio.Queue[bytes]] = []
_prices: dict[int, dict[str, dict[str, float]]] = {}
_prices_lock = asyncio.Lock()
_snapshot_bytes: bytes = b"{}"
_sse_update_count = 0


def _build_snapshot_bytes() -> bytes:
    """Serialize current in-memory snapshot once per update (zero-copy for SSE clients)."""
    snapshot = {
        "type": "snapshot",
        "prices": {
            str(mid): {
                market: {sel: odd for sel, odd in selections.items()}
                for market, selections in markets.items()
            }
            for mid, markets in _prices.items()
        },
        "ts": time.time(),
    }
    return json.dumps(snapshot).encode()


# ── SSE helpers ─────────────────────────────────────────────────


async def _sse_generator(q: asyncio.Queue[bytes]):
    """Async generator for a single SSE client. Yields initial snapshot, then updates."""
    # Send initial snapshot
    yield b"event: snapshot\ndata: " + _snapshot_bytes + b"\n\n"

    while True:
        try:
            data = await asyncio.wait_for(q.get(), timeout=25.0)
            yield b"data: " + data + b"\n\n"
        except asyncio.TimeoutError:
            yield b": keepalive\n\n"


# ── Lifespan ────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _subscribers, _prices, _snapshot_bytes, _sse_update_count

    # Startup
    repo = SQLiteRepository()
    repo.start()

    session = make_session()
    client = KambiClient(session)

    feed_queue: asyncio.Queue[OddsUpdate] = asyncio.Queue(maxsize=settings.feed_queue_maxsize)

    manager = FeedManager(client, feed_queue)
    manager_task = asyncio.create_task(manager.run())
    consumer_task = asyncio.create_task(_consume_feed(feed_queue, repo, manager))

    app.state.repo = repo
    app.state.session = session
    app.state.manager = manager
    app.state.feed_queue = feed_queue
    app.state.manager_task = manager_task
    app.state.consumer_task = consumer_task

    logger.info("PMU Sport server started on port %d", settings.port)

    yield

    # Shutdown
    await manager.shutdown()
    manager_task.cancel()
    consumer_task.cancel()
    try:
        await asyncio.gather(manager_task, consumer_task, return_exceptions=True)
    except Exception:
        pass

    repo.stop()
    repo.join(timeout=10.0)
    await session.close()
    logger.info("PMU Sport server shut down")


# ── Feed consumer (single consumer of shared queue) ─────────────


async def _consume_feed(
    queue: asyncio.Queue[OddsUpdate],
    repo: PriceRepository,
    manager: FeedManager,
) -> None:
    """Single consumer: in-memory prices → SSE fan-out → SQLite enqueue."""
    global _prices, _snapshot_bytes, _sse_update_count

    while True:
        update: OddsUpdate = await queue.get()

        # 1. Update in-memory prices
        async with _prices_lock:
            mid = update.match_id
            if mid not in _prices:
                _prices[mid] = {}
            _prices[mid][update.market] = update.odds

            _snapshot_bytes = _build_snapshot_bytes()

        # 2. Pre-serialize event for SSE
        event_data = json.dumps({
            "type": "price",
            "match_id": update.match_id,
            "market": update.market,
            "odds": update.odds,
            "movements": update.movements,
            "meta": update.meta.model_dump() if update.meta else None,
            "score": update.score.model_dump() if update.score else None,
            "live": update.live,
            "ts": update.ts,
        }).encode()

        # 3. Fan-out to SSE subscribers
        for q in list(_subscribers):
            try:
                q.put_nowait(event_data)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(event_data)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    _subscribers.remove(q)

        _sse_update_count += 1

        # 4. Persist to SQLite (non-blocking)
        repo.enqueue(update)


# ── FastAPI app ─────────────────────────────────────────────────

app = FastAPI(title="PMU SPORT Odds Monitor", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/stream")
async def stream_events():
    """SSE endpoint: sends initial snapshot then pushes odds updates.

    Event types:
      - event: snapshot  (initial full state)
      - data: {...}      (price update)
      - : keepalive      (25s heartbeat)
    """
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
    _subscribers.append(q)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }

    async def event_stream():
        try:
            async for event in _sse_generator(q):
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            if q in _subscribers:
                _subscribers.remove(q)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@app.get("/prices")
async def get_prices():
    """Current in-memory odds snapshot: {match_id: {market: {selection: odd}}}."""
    async with _prices_lock:
        return {
            str(mid): {
                market: dict(selections) for market, selections in markets.items()
            }
            for mid, markets in _prices.items()
        }


@app.get("/markets")
async def get_markets():
    """Match metadata for all active matches (intersects with in-memory prices)."""
    manager: FeedManager = app.state.manager
    results: list[dict] = []
    for mid, meta in manager.meta.items():
        results.append(meta.model_dump())
    return {"matches": results}


@app.get("/status")
async def get_status():
    """Feed health: streams, updates, SSE clients, matches in memory."""
    return {
        "sse_clients": len(_subscribers),
        "sse_updates_pushed": _sse_update_count,
        "matches_in_memory": len(_prices),
        "matches_known": len(app.state.manager.meta),
        "feed_updates": app.state.manager.updates_pushed,
    }


@app.get("/history")
async def get_history(
    match_id: int = Query(...),
    selection: str = Query(...),
    market: str = Query("Match Odds"),
    limit: int = Query(500, ge=1, le=2000),
):
    """Price history from SQLite (oldest first)."""
    repo: PriceRepository = app.state.repo
    points = await asyncio.get_running_loop().run_in_executor(
        None, lambda: repo.get_history(match_id, selection, market, limit)
    )
    return {
        "match_id": match_id,
        "selection": selection,
        "market": market,
        "points": [p.model_dump() for p in points],
    }


@app.get("/")
@app.get("/dashboard.html")
async def serve_dashboard():
    return FileResponse("frontend/dashboard.html")


@app.get("/chart.html")
async def serve_chart():
    return FileResponse("frontend/chart.html")


@app.get("/stream.html")
async def serve_stream_log():
    return FileResponse("frontend/stream.html")
