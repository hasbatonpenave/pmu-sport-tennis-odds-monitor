# Kambi Socket.IO Push Protocol

## Connection
- Endpoint: `wss://push-eu.offering-api.kambicdn.com/socket.io/?EIO=4&transport=websocket`
- Library: `python-socketio[asyncio_client]`, EIO=4
- Header: `Origin: https://www.pmu.fr`
- Auth: none
- SSL: verify disabled (self-signed/intermediate cert issues)

## Subscription
- Subscribe: `emit("subscribe", {"topic": "v2018.pmusportsfr.fr.ev.{eventId}.json"})`
- Unsubscribe: `emit("unsubscribe", {"topic": "v2018.pmusportsfr.fr.ev.{eventId}.json"})`
- Topic prefix: `v2018.pmusportsfr.fr.ev`
- No global/wildcard topic — per-event only

## Incoming event
- Event name: `"message"` (standard Socket.IO message event)
- Payload: double-encoded JSON string → `json.loads()` once → list of frame dicts

## Message types (mt)

| mt | key  | meaning                    | has_odds | key_fields                                |
|----|------|----------------------------|----------|-------------------------------------------|
| 6  | boa  | Bet Offer Amendment        | YES      | boa.betOffer.eventId, criterion, outcomes |
| 8  | bosu | Bet Offer Suspension       | no       | bosu.betOfferId, eventId, suspended       |
| 22 | booa | Bet Offer Outcome Adjust   | no       | booa.eventId, outcomes[].status           |

### mt=6 — Bet Offer Amendment (primary odds update)

This is the main odds-bearing frame. It carries a full bet offer snapshot
including all outcomes with current odds and status.

**Field path:**
```
frame["boa"]["betOffer"]["eventId"]          → int: match/event ID
frame["boa"]["betOffer"]["suspended"]        → bool: market-level suspension
frame["boa"]["betOffer"]["criterion"]["englishLabel"]  → str: market name
frame["boa"]["betOffer"]["criterion"]["label"]         → str: market name (fr)
frame["boa"]["betOffer"]["betOfferType"]["id"]         → int: market type (1=HCP, 2=Match, 6=O/U, 18=Y/N)
frame["boa"]["betOffer"]["betOfferType"]["englishName"] → str: market type name
frame["boa"]["betOffer"]["outcomes"][i]["id"]          → int
frame["boa"]["betOffer"]["outcomes"][i]["label"]       → str: selection name (fr)
frame["boa"]["betOffer"]["outcomes"][i]["englishLabel"] → str: selection name
frame["boa"]["betOffer"]["outcomes"][i]["odds"]        → int: odds × 1000 (e.g., 23000 = 23.00)
frame["boa"]["betOffer"]["outcomes"][i]["line"]        → int | null: line × 1000 (for O/U, HCP)
frame["boa"]["betOffer"]["outcomes"][i]["type"]        → str: OT_ONE, OT_TWO, OT_OVER, OT_UNDER
frame["boa"]["betOffer"]["outcomes"][i]["status"]      → str: "OPEN" | "SUSPENDED"
frame["boa"]["betOffer"]["outcomes"][i]["changedDate"] → str: ISO 8601 last change
```

**Odds format:** integer × 1000 → divide by 1000 for decimal (consistent with `_odds_int_to_float` in parser.py).

**Suspension:** when `betOffer["suspended"]` is true OR all outcomes have `status: "SUSPENDED"`, the market is suspended. When OPEN, outcomes carry an `odds` field.

### mt=8 — Bet Offer Suspension (lightweight toggle)

Sent frequently (most common frame). No odds data.
```
frame["bosu"]["betOfferId"]  → int
frame["bosu"]["eventId"]     → int
frame["bosu"]["suspended"]   → bool
```

### mt=22 — Bet Offer Outcome Adjustment (rare)

Outcome-level status changes without odds. No odds field.
```
frame["booa"]["eventId"]      → int
frame["booa"]["outcomes"][i]  → status/cashOut changes
```

## Observed latency
- Connection setup: ~2-3s
- First message after subscribe: ~5-10s
- Subsequent mt=8 (suspension): 1-5 per second
- mt=6 (odds): 1-3 per second during active play
- mt=22 (outcome adj): rare, ~2 per 2 min

## Notes
- Tennis-specific mt types (score, statistics) not yet observed due to no live tennis events at probe time
- Frame keys: `boa` = BetOfferAmendment, `bosu` = BetOfferSuspension, `booa` = BetOfferOutcomeAdjustment
- All frames have `t` (timestamp, epoch ms as string) and `mt` (message type) fields
