from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PMU_SPORT_")

    # Server
    port: int = 5500

    # Kambi API
    kambi_base_url: str = "https://eu.offering-api.kambicdn.com/offering/v2018/pmusportsfr"
    locale: str = "fr_MC"
    market: str = "FR"
    client_id: int = 200
    channel_id: int = 1

    # Database
    db_path: str = "pmu_tennis.db"

    # Feed
    poll_interval_s: float = 2.5
    match_refresh_min: float = 5.0
    reconnect_delay_s: float = 5.0
    max_match_age_h: float = 48.0
    max_streams_per_host: int = 20
    feed_queue_maxsize: int = 20_000

    # Tracked markets (criterion englishLabel or betOfferType match)
    tracked_markets: list[str] = [
        "Match Odds",
        "Total Games",
        "Set Betting",
        "Game Handicap",
        "Total Sets",
    ]

    # Circuit breaker
    cb_max_failures: int = 5
    cb_reset_after_s: float = 300.0

    # Logging
    log_level: str = "INFO"


settings = Settings()
