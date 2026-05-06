import logging
import ssl

import aiohttp

from config import settings

logger = logging.getLogger(__name__)

_LIST_VIEW_URL = (
    f"{settings.kambi_base_url}/listView/tennis/all/all/all/matches.json"
    f"?lang={settings.locale}&market={settings.market}"
    f"&client_id={settings.client_id}&channel_id={settings.channel_id}"
    f"&useCombined=true&useCombinedLive=true"
)

_LIVE_OPEN_URL = (
    f"{settings.kambi_base_url}/event/live/open.json"
    f"?lang={settings.locale}&market={settings.market}"
    f"&client_id={settings.client_id}&channel_id={settings.channel_id}"
)

_BETOFFER_URL = (
    f"{settings.kambi_base_url}/betoffer/event/{{event_id}}.json"
    f"?lang={settings.locale}&market={settings.market}"
    f"&client_id={settings.client_id}&channel_id={settings.channel_id}"
)


def make_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(
        limit=settings.max_streams_per_host,
        limit_per_host=settings.max_streams_per_host,
        ttl_dns_cache=600,
        enable_cleanup_closed=True,
        verify_ssl=False,
    )
    timeout = aiohttp.ClientTimeout(total=30, connect=10)
    headers = {
        "Accept": "application/json",
        "Accept-Language": "fr,en-US;q=0.9,en;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
    }
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return aiohttp.ClientSession(
        connector=connector,
        timeout=timeout,
        headers=headers,
        cookie_jar=aiohttp.DummyCookieJar(),
    )


class KambiClient:
    """Async HTTP client for the Kambi / PMU SPORT offering API."""

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session

    async def list_matches(self) -> dict:
        """Fetch all tennis matches with combined odds (live + pre-match)."""
        async with self._session.get(_LIST_VIEW_URL) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_live_events(self, ncid: int = 0) -> dict:
        """Fetch live events with scores and main bet offers."""
        url = f"{_LIVE_OPEN_URL}&ncid={ncid}"
        async with self._session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_betoffers(self, event_id: int) -> dict:
        """Fetch all bet offers (deep markets) for a specific event."""
        url = _BETOFFER_URL.format(event_id=event_id)
        async with self._session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()
