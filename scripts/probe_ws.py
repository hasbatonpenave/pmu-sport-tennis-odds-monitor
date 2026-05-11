"""
Probe the PMU Socket.IO feed to discover all mt types and odds field paths.
Run from the project root: python scripts/probe_ws.py
"""
import asyncio
import json
import pathlib
import time
from collections import Counter

import requests
import socketio
import urllib3
urllib3.disable_warnings()

pathlib.Path("fixtures").mkdir(exist_ok=True)

sio = socketio.AsyncClient(logger=False, engineio_logger=False, ssl_verify=False)
seen_mt: dict[int, dict] = {}
mt_counts: Counter = Counter()
event_frames: list[dict] = []  # All frames for first event
msg_count = 0
start_time = 0


@sio.event
async def connect():
    global start_time
    start_time = time.time()
    print(f"=== CONNECTED ===")
    for eid in LIVE_EVENT_IDS:
        topic = f"v2018.pmusportsfr.fr.ev.{eid}.json"
        await sio.emit("subscribe", {"topic": topic})
        print(f"[subscribed to event {eid}]")


@sio.event
async def disconnect():
    print("=== DISCONNECTED ===")


@sio.on("*")
async def catch_all(event, data):
    global msg_count
    msg_count += 1
    elapsed = time.time() - start_time

    # Double-decode
    try:
        frames = json.loads(data) if isinstance(data, str) else data
    except Exception:
        return

    if not isinstance(frames, list):
        return

    for frame in frames:
        if not isinstance(frame, dict):
            continue

        mt = frame.get("mt")
        mt_counts[mt] += 1

        # Save first occurrence of each mt
        if mt not in seen_mt:
            seen_mt[mt] = frame
            path = f"fixtures/mt_{mt}.json"
            with open(path, "w") as fh:
                json.dump(frame, fh, indent=2, ensure_ascii=False)
            print(f"[{elapsed:.0f}s] mt={mt} FIRST | keys={list(frame.keys())} | SAVED to {path}")

        # Store all frames for the first event
        eid = _extract_event_id(frame)
        if eid and eid == LIVE_EVENT_IDS[0]:
            event_frames.append(frame)

        if mt == 6 and mt_counts[mt] <= 3:
            # Print odds summary for first few mt=6
            bo = frame.get("boa", {}).get("betOffer", {})
            outcomes = bo.get("outcomes", [])
            odds_summary = {
                o.get("englishLabel", "?"): o.get("odds")
                for o in outcomes if o.get("odds")
            }
            label = bo.get("criterion", {}).get("englishLabel", "?")
            print(f"[{elapsed:.0f}s] mt=6 #{mt_counts[mt]} | market={label} | odds={odds_summary}")

        if len(seen_mt) >= 10:
            print(f"\n=== DONE: {len(seen_mt)} mt types in {elapsed:.0f}s ===")
            await sio.disconnect()
            return


def _extract_event_id(frame: dict) -> int | None:
    """Extract event ID from any frame type."""
    for key in ("boa", "bosu", "booa"):
        node = frame.get(key, {})
        eid = node.get("eventId") or node.get("betOffer", {}).get("eventId")
        if eid:
            return eid
    return None


async def main():
    print(f"Subscribing to {len(LIVE_EVENT_IDS)} live events: {LIVE_EVENT_IDS}")
    await sio.connect(
        "https://push-eu.offering-api.kambicdn.com",
        headers={"Origin": "https://www.pmu.fr"},
        transports=["websocket"],
        wait_timeout=15,
    )
    await asyncio.sleep(120)  # 2 minutes to capture more mt types
    elapsed = time.time() - start_time
    print(f"\n=== TIMEOUT after {elapsed:.0f}s ===")
    print(f"Messages received: {msg_count}")
    print(f"mt counts: {dict(mt_counts.most_common())}")

    # Save all frames for first event
    if event_frames:
        path = f"fixtures/event_{LIVE_EVENT_IDS[0]}_all.json"
        with open(path, "w") as fh:
            json.dump(event_frames, fh, indent=2, ensure_ascii=False)
        print(f"Saved {len(event_frames)} frames to {path}")

    await sio.disconnect()


if __name__ == "__main__":
    print("Fetching live event IDs...")
    r = requests.get(
        "https://eu.offering-api.kambicdn.com/offering/v2018/pmusportsfr/"
        "event/live/open.json"
        "?lang=fr_MC&market=FR&client_id=200&channel_id=1",
        verify=False,
    )
    data = r.json()
    LIVE_EVENT_IDS = [
        le.get("event", {}).get("id")
        for le in data.get("liveEvents", [])[:5]
        if le.get("event", {}).get("id")
    ]
    print(f"Live event IDs: {LIVE_EVENT_IDS}")
    asyncio.run(main())
