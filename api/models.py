import time
from typing import Optional

from pydantic import BaseModel, Field


class MatchMeta(BaseModel):
    match_id: int
    name: str
    player_a: str
    player_b: str
    competition: str
    start_time: str  # ISO 8601
    state: str  # STARTED, NOT_STARTED, CLOSED
    path: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ScoreState(BaseModel):
    home: str = "0"
    away: str = "0"
    sets_home: list[int] = Field(default_factory=list)
    sets_away: list[int] = Field(default_factory=list)
    home_serve: Optional[bool] = None
    period_desc: Optional[str] = None


class OddsUpdate(BaseModel):
    match_id: int
    market: str  # "Match Odds", "Total Games", etc.
    odds: dict[str, float]  # selection_name -> decimal odds
    movements: dict[str, str] = Field(default_factory=dict)  # selection_name -> "up"|"down"|"steady"|"new"
    meta: Optional[MatchMeta] = None
    score: Optional[ScoreState] = None
    live: bool = False
    ts: float = Field(default_factory=time.time)


class PricePoint(BaseModel):
    ts: float
    match_id: int
    market: str
    selection: str
    odd: float


class SSEEvent(BaseModel):
    type: str  # "snapshot" or "price"
    payload: dict
