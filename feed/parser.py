"""Parse Kambi JSON responses into internal models.

Kambi API data format:
  - Odds are integer × 100 (6100 = 61.00). Divide by 100 for display.
  - outcome.type: OT_ONE (home), OT_TWO (away), OT_OVER, OT_UNDER
  - betOfferType.id: 1=Handicap, 2=Match, 3=Correct Score, 6=Over/Under, 18=Yes/No
  - liveData.statistics.sets = [set1_games, set2_games, set3_games] with -1 = not played
  - outcome.changedDate = ISO 8601 timestamp of last odds change
"""

import logging
import time
from typing import Optional

from api.models import MatchMeta, OddsUpdate, ScoreState

logger = logging.getLogger(__name__)

# betOfferType.id -> canonical market name prefix
_MARKET_TYPE_MAP = {
    1: "Handicap",
    2: "Match",
    3: "Correct Score",
    6: "Over/Under",
    18: "Yes/No",
}

# criterion.englishLabel patterns — filtering is now done per-sport via SPORT_MARKETS


def _odds_int_to_float(odds_int: int) -> float:
    return odds_int / 1000.0


def _parse_score(live_data: dict) -> Optional[ScoreState]:
    """Extract score state from Kambi liveData block."""
    if not live_data:
        return None

    score_block = live_data.get("score", {})
    stats = live_data.get("statistics", {})
    sets_block = stats.get("sets", {})

    sets_home = sets_block.get("home", [])
    sets_away = sets_block.get("away", [])

    return ScoreState(
        home=str(score_block.get("home", "0")),
        away=str(score_block.get("away", "0")),
        sets_home=[s for s in sets_home if isinstance(s, int)],
        sets_away=[s for s in sets_away if isinstance(s, int)],
        home_serve=sets_block.get("homeServe"),
    )


def _extract_path(event: dict) -> list[dict]:
    path = event.get("path", [])
    return [{"id": p["id"], "name": p.get("name", ""), "termKey": p.get("termKey", "")} for p in path]


def parse_match_list(
    data: dict,
    sport_name: str,
    tracked_markets: list[str],
) -> tuple[dict[int, MatchMeta], dict[int, dict[str, dict[str, float]]]]:
    """Parse listView response.

    Returns (meta_by_id, prices_by_id) where prices is {match_id: {market_name: {selection: odd}}}.
    """
    meta_by_id: dict[int, MatchMeta] = {}
    prices_by_id: dict[int, dict[str, dict[str, float]]] = {}

    for entry in data.get("events", []):
        event = entry.get("event", {})
        match_id = event.get("id")
        if not match_id:
            continue

        # Only process events matching the requested sport
        if event.get("sport") != sport_name:
            continue

        state = event.get("state", "NOT_STARTED")

        meta = MatchMeta(
            match_id=match_id,
            name=event.get("name", ""),
            player_a=event.get("homeName", ""),
            player_b=event.get("awayName", ""),
            competition=event.get("group", ""),
            start_time=event.get("start", ""),
            state=state,
            sport=sport_name,
            path=_extract_path(event),
            tags=event.get("tags", []),
        )
        meta_by_id[match_id] = meta

        # Parse bet offers
        market_odds: dict[str, dict[str, float]] = {}
        for bo in entry.get("betOffers", []):
            criterion = bo.get("criterion", {})
            bo_type = bo.get("betOfferType", {})
            label = criterion.get("englishLabel", "")

            # Only track relevant markets
            if not any(t in label for t in tracked_markets):
                continue

            # Build market key: include line for O/U and handicap
            market_key = label
            line = None
            for outcome in bo.get("outcomes", []):
                if "line" in outcome:
                    line = outcome["line"] / 1000.0
                    break
            if line is not None:
                market_key = f"{label} {line:g}"

            selections: dict[str, float] = {}
            for outcome in bo.get("outcomes", []):
                if outcome.get("status") != "OPEN":
                    continue
                odd_int = outcome.get("odds")
                if odd_int is None:
                    continue
                sel_label = outcome.get("englishLabel") or outcome.get("label", "")
                selections[sel_label] = _odds_int_to_float(odd_int)

            if selections:
                market_odds[market_key] = selections

        if market_odds:
            prices_by_id[match_id] = market_odds

    return meta_by_id, prices_by_id


def parse_live_events(
    data: dict,
    accepted_sports: list[str] | None = None,
) -> list[OddsUpdate]:
    """Parse live/open.json response into OddsUpdate list (only changed events).

    Returns updates for live matches with mainBetOffer + liveData.
    """
    updates: list[OddsUpdate] = []

    for entry in data.get("liveEvents", []):
        event = entry.get("event", {})
        sport = event.get("sport", "")

        if accepted_sports and sport not in accepted_sports:
            continue

        match_id = event.get("id")
        if not match_id:
            continue

        main_bo = entry.get("mainBetOffer", {})
        live_data = entry.get("liveData", {})

        criterion = main_bo.get("criterion", {})
        label = criterion.get("englishLabel", "") or "Match Odds"

        odds: dict[str, float] = {}
        for outcome in main_bo.get("outcomes", []):
            if outcome.get("status") != "OPEN":
                continue
            odd_int = outcome.get("odds")
            if odd_int is None:
                continue
            sel_label = outcome.get("englishLabel") or outcome.get("label", "")
            odds[sel_label] = _odds_int_to_float(odd_int)

        if not odds:
            continue

        score = _parse_score(live_data)

        update = OddsUpdate(
            match_id=match_id,
            market=label,
            odds=odds,
            meta=MatchMeta(
                match_id=match_id,
                name=event.get("name", ""),
                player_a=event.get("homeName", ""),
                player_b=event.get("awayName", ""),
                competition=event.get("group", ""),
                start_time=event.get("start", ""),
                state=event.get("state", "NOT_STARTED"),
                sport=sport,
                path=_extract_path(event),
                tags=event.get("tags", []),
            ),
            score=score,
            live=True,
        )
        updates.append(update)

    return updates


def parse_betoffers(
    data: dict,
    match_id: int,
    tracked_markets: list[str],
) -> list[OddsUpdate]:
    """Parse betoffer/event/{id}.json response into OddsUpdate list.

    Extracts all tracked markets from the detailed bet offer endpoint.
    """
    updates: list[OddsUpdate] = []

    for bo in data.get("betOffers", []):
        criterion = bo.get("criterion", {})
        label = criterion.get("englishLabel", "")

        if not any(t in label for t in tracked_markets):
            continue

        market_key = label
        line = None
        outcomes = bo.get("outcomes", [])
        for outcome in outcomes:
            if "line" in outcome:
                line = outcome["line"] / 1000.0
                break
        if line is not None:
            market_key = f"{label} {line:g}"

        odds: dict[str, float] = {}
        for outcome in outcomes:
            if outcome.get("status") != "OPEN":
                continue
            odd_int = outcome.get("odds")
            if odd_int is None:
                continue
            sel_label = outcome.get("englishLabel") or outcome.get("label", "")
            odds[sel_label] = _odds_int_to_float(odd_int)

        if odds:
            updates.append(OddsUpdate(match_id=match_id, market=market_key, odds=odds))

    return updates


def diff_odds(
    match_id: int,
    market: str,
    current: dict[str, float],
    previous: dict[str, float],
) -> tuple[dict[str, float], dict[str, str]]:
    """Compare current odds against previous snapshot.

    Returns (changed_odds, movements) where movements maps selection -> "up"|"down"|"steady"|"new".
    Only selections that actually changed are included in changed_odds.
    """
    changed: dict[str, float] = {}
    movements: dict[str, str] = {}

    for sel, odd in current.items():
        prev = previous.get(sel)
        if prev is None:
            changed[sel] = odd
            movements[sel] = "new"
        elif abs(odd - prev) > 0.001:
            changed[sel] = odd
            movements[sel] = "up" if odd > prev else "down"

    return changed, movements
