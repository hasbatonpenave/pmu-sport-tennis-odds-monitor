#!/usr/bin/env python3
"""PMU SPORT Tennis Odds Monitor — entry point.

Usage:
    python server.py
    PMU_SPORT_PORT=8080 python server.py
"""

import uvicorn

from config import settings

if __name__ == "__main__":
    uvicorn.run(
        "server.app:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )
