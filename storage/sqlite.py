import logging
import queue
import sqlite3
import threading
import time

from api.models import OddsUpdate, PricePoint
from config import settings
from storage.repository import PriceRepository

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pmu_prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL    NOT NULL,
    match_id    INTEGER NOT NULL,
    market      TEXT    NOT NULL,
    selection   TEXT    NOT NULL,
    odd         REAL    NOT NULL,
    match_name  TEXT,
    player_a    TEXT,
    player_b    TEXT,
    competition TEXT,
    start_time  TEXT,
    is_live     INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pmu_match_ts ON pmu_prices(match_id, ts);
CREATE INDEX IF NOT EXISTS idx_pmu_market_sel ON pmu_prices(market, selection);
"""


class SQLiteRepository(PriceRepository):
    """SQLite-backed price repository with background batch writer.

    - WAL mode for concurrent read/write
    - Batched writes: flush every 500ms or 100 rows
    - Reads open a fresh connection (thread-safe)
    """

    def __init__(self, db_path: str = ""):
        self._db_path = db_path or settings.db_path
        self._write_queue: queue.Queue[OddsUpdate | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._running = False

    # ── public API ──────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="pmu-db-writer",
            daemon=True,
        )
        self._thread.start()
        self._init_db()

    def stop(self) -> None:
        self._running = False
        self._write_queue.put(None)  # sentinel

    def join(self, timeout: float = 10.0) -> None:
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def enqueue(self, update: OddsUpdate) -> None:
        """Non-blocking: push to write queue (called from async event loop)."""
        if self._running:
            self._write_queue.put(update)

    def get_history(
        self,
        match_id: int,
        selection: str,
        market: str,
        limit: int = 500,
    ) -> list[PricePoint]:
        """Fetch price history oldest-first (called via run_in_executor)."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT ts, match_id, market, selection, odd
                FROM pmu_prices
                WHERE match_id = ? AND market = ? AND selection = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (match_id, market, selection, limit),
            ).fetchall()
            # Reverse to oldest-first for chart display
            return [
                PricePoint(
                    ts=row["ts"],
                    match_id=row["match_id"],
                    market=row["market"],
                    selection=row["selection"],
                    odd=row["odd"],
                )
                for row in reversed(rows)
            ]
        finally:
            conn.close()

    # ── internals ───────────────────────────────────────────────

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript("PRAGMA journal_mode=WAL;")
            conn.executescript("PRAGMA synchronous=NORMAL;")
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    def _writer_loop(self) -> None:
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        batch: list[OddsUpdate] = []
        last_flush = 0.0

        try:
            while True:
                try:
                    item = self._write_queue.get(timeout=0.5)
                except queue.Empty:
                    item = None

                if item is None:
                    # Sentinel or timeout — flush
                    self._flush(conn, batch)
                    batch.clear()
                    if not self._running:
                        break
                    continue

                batch.append(item)
                now = time.monotonic()

                if len(batch) >= 100 or (batch and now - last_flush >= 0.5):
                    self._flush(conn, batch)
                    batch.clear()
                    last_flush = now

        except Exception as exc:
            logger.error("DB writer error: %s", exc)
        finally:
            self._flush(conn, batch)
            conn.close()

    def _flush(self, conn: sqlite3.Connection, batch: list[OddsUpdate]) -> None:
        if not batch:
            return
        rows = []
        for u in batch:
            for sel, odd in u.odds.items():
                rows.append((
                    u.ts,
                    u.match_id,
                    u.market,
                    sel,
                    odd,
                    u.meta.name if u.meta else "",
                    u.meta.player_a if u.meta else "",
                    u.meta.player_b if u.meta else "",
                    u.meta.competition if u.meta else "",
                    u.meta.start_time if u.meta else "",
                    1 if u.live else 0,
                ))
        if rows:
            try:
                conn.executemany(
                    """
                    INSERT INTO pmu_prices (ts, match_id, market, selection, odd,
                        match_name, player_a, player_b, competition, start_time, is_live)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.commit()
            except Exception as exc:
                logger.error("DB flush failed: %s", exc)
