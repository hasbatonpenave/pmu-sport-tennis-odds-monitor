# PMU SPORT Tennis Odds Monitor

Near-real-time tennis odds monitoring platform for PMU SPORT, built on top of the Kambi
offering API. Polls the Kambi REST API every 2–3 seconds, diffs odds changes, and pushes
them to browser dashboards via Server-Sent Events.

**End-to-end odds latency: 0–3 seconds.** This is determined by the Kambi API poll
interval, not the application architecture. The system cannot go faster without a
push-based API.

## Architecture

```
Kambi REST API ──(aiohttp poll)──> FeedManager ──> asyncio.Queue[OddsUpdate]
                                                              │
                                                       consume_feed()          ← SINGLE consumer
                                                       /           \
                              in-memory prices snapshot             fan-out
                              (for /prices, /markets)       /              \
                                                   per-client queues    SQLite batch writer
                                                   (one per SSE client)  (background thread)
```

`consume_feed()` is the sole consumer of the shared queue. It distributes each update to
in-memory state, every connected SSE client, and the SQLite background writer.

## Quick Start

```bash
pip install -r requirements.txt
python server.py
```

Open http://localhost:5003/stream in a browser, or open the dashboards:

```bash
open frontend/dashboard.html   # Live odds table
open frontend/chart.html       # Price history chart
open frontend/stream.html      # Raw SSE event log
```

## Configuration

All settings via environment variables with `PMU_SPORT_` prefix:

| Variable | Default | Description |
|---|---|---|
| `PMU_SPORT_PORT` | `5003` | Server port |
| `PMU_SPORT_DB_PATH` | `pmu_tennis.db` | SQLite database path |
| `PMU_SPORT_POLL_INTERVAL_S` | `2.5` | Live odds poll interval (seconds) |
| `PMU_SPORT_MATCH_REFRESH_MIN` | `5.0` | Match list refresh interval (minutes) |
| `PMU_SPORT_MAX_MATCH_AGE_H` | `48.0` | Maximum match age to track (hours) |
| `PMU_SPORT_CB_MAX_FAILURES` | `5` | Circuit breaker failure threshold |
| `PMU_SPORT_CB_RESET_AFTER_S` | `300.0` | Circuit breaker park duration (seconds) |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/stream` | SSE with initial snapshot then push updates (25s keepalive) |
| GET | `/prices` | Current in-memory odds `{match_id: {market: {selection: odd}}}` |
| GET | `/markets` | Match metadata for all active matches |
| GET | `/status` | Feed health (SSE clients, updates pushed, matches in memory) |
| GET | `/history` | Price history from SQLite. Params: `match_id`, `selection`, `market`, `limit` |

## SSE Event Format

Initial snapshot:
```json
event: snapshot
data: {"prices": {"123": {"Match Odds": {"Player A": 1.85, "Player B": 2.10}}}, "ts": 1700000000.0}
```

Price update:
```json
data: {"type":"price","match_id":123,"market":"Match Odds","odds":{"Player A":1.82,...},"movements":{"Player A":"down",...},"meta":{...},"score":{...},"live":true,"ts":1700000001.0}
```

## Kambi API Notes

PMU SPORT uses the Kambi sportsbook platform. The offering API is public (no auth).

Key endpoints:
- `GET /offering/v2018/pmusportsfr/listView/tennis/all/all/all/matches.json` — match listing
- `GET /offering/v2018/pmusportsfr/event/live/open.json` — live events with scores
- `GET /offering/v2018/pmusportsfr/betoffer/event/{id}.json` — detailed markets

Odds are integer × 100 (6100 = 61.00). Scores use `[set1, set2, set3]` arrays with `-1`
for unplayed sets. Live matches have `state: "STARTED"`.

## File Map

| Path | Role |
|---|---|
| `config.py` | pydantic-settings (`PMU_SPORT_` env prefix) |
| `server.py` | Entry point (uvicorn) |
| `api/client.py` | Kambi HTTP client (aiohttp) |
| `api/models.py` | Pydantic data contracts |
| `feed/parser.py` | Kambi JSON → OddsUpdate parsing |
| `feed/stream.py` | Circuit breaker + live odds polling |
| `feed/manager.py` | Feed orchestrator (match discovery, task management) |
| `server/app.py` | FastAPI app, SSE fan-out, REST endpoints |
| `storage/sqlite.py` | SQLite with WAL mode, background batch writer (500ms flush) |
| `frontend/dashboard.html` | Live odds table |
| `frontend/chart.html` | Chart.js price history |
| `frontend/stream.html` | Raw SSE event log |

## Tracked Markets

- Match winner (Moneyline)
- Total games Over/Under
- Set betting (correct set score)
- Game handicap
- Total sets

Additional markets are available via the Kambi betoffer detail endpoint but not polled
by default to limit API load.
