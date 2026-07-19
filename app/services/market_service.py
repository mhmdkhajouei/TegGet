"""
Business logic layer for market data derived metrics and fetching.
Combined fetcher and recorder to keep dependencies clean.
"""
import asyncio
import logging
import time
from typing import Optional, Union, TypedDict

import httpx

from app.database import get_db
from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)

# حالت‌های مدیریت زاپاس (Failover State)
_wallex_fail_count = 0
_fallback_active = False
_fallback_started_at = 0.0
_COOLDOWN_PERIOD = 120.0  # مدت زمان مجازات والکس (۲ دقیقه)

TOLERANCE_SECONDS = 60 * 60  # +/- 60 minutes


class MarketData(TypedDict):
    price: float
    volume: Optional[float]
    source: str
    change_24h: Optional[float]


def _debug_dump(source: str, data) -> None:
    if isinstance(data, dict):
        preview = f"dict with keys: {list(data.keys())}"
    elif isinstance(data, list):
        sample = data[0] if data else None
        preview = f"list of {len(data)} items, first item: {sample!r}"[:500]
    else:
        preview = repr(data)[:500]
    logger.error("%s response shape did not match expectations -> %s", source, preview)


async def _fetch_wallex() -> MarketData:
    """Priority 1: Wallex API — symbol USDTTMN, price/volume under stats."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(settings.wallex_api_url)
        resp.raise_for_status()
        data = resp.json()

    try:
        stats = data["result"]["symbols"]["USDTTMN"]["stats"]
        price = float(stats["lastPrice"])
        volume_tmn = stats.get("24h_tmnVolume")
        change_24h = stats.get("24h_ch")
    except (KeyError, TypeError):
        _debug_dump("wallex", data)
        raise

    return MarketData(
        price=price,
        volume=float(volume_tmn) if volume_tmn is not None else None,
        source="wallex",
        change_24h=float(change_24h) if change_24h is not None else None,
    )


async def _fetch_bitpin() -> MarketData:
    """Priority 2: Bitpin API."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(settings.bitpin_api_url)
        resp.raise_for_status()
        payload = resp.json()

    try:
        markets = payload["results"]
        market = next(
            m
            for m in markets
            if m["currency1"]["code"] == "USDT" and m["currency2"]["code"] == "IRT"
        )
        price = float(market["price"])
        volume = None
        order_book_info = market.get("order_book_info") or {}
        if order_book_info.get("amount") is not None:
            volume = float(order_book_info["amount"])
    except (KeyError, TypeError, StopIteration):
        _debug_dump("bitpin", payload)
        raise

    return MarketData(price=price, volume=volume, source="bitpin", change_24h=None)


async def _fetch_tetherland() -> MarketData:
    """Priority 3: TetherLand API."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(settings.tetherland_api_url)
        resp.raise_for_status()
        data = resp.json()

    try:
        price = float(data["currencies"]["USDT"]["price"])
    except (KeyError, TypeError):
        _debug_dump("tetherland", data)
        raise

    return MarketData(price=price, volume=None, source="tetherland", change_24h=None)


_FETCHERS = {
    "wallex": _fetch_wallex,
    "bitpin": _fetch_bitpin,
    "tetherland": _fetch_tetherland,
}


async def get_market_data() -> Optional[MarketData]:
    """
    Fetches market data with a resilient stateful fallback chain.
    """
    global _wallex_fail_count, _fallback_active, _fallback_started_at

    now = time.time()

    if _fallback_active and (now - _fallback_started_at > _COOLDOWN_PERIOD):
        logger.info("Auto-recovery: Trying to restore Wallex as the primary market source.")
        _fallback_active = False
        _wallex_fail_count = 0

    priority = list(settings.market_source_priority)
    if _fallback_active and "wallex" in priority:
        priority.remove("wallex")
        priority.append("wallex")

    for source_name in priority:
        fetcher = _FETCHERS.get(source_name)
        if fetcher is None:
            logger.warning("No fetcher registered for source %s", source_name)
            continue

        try:
            data = await fetcher()
            if source_name == "wallex":
                _wallex_fail_count = 0
                _fallback_active = False
            return data
        except Exception:
            logger.warning("Market source %s failed this tick", source_name)
            if source_name == "wallex":
                _wallex_fail_count += 1
                if _wallex_fail_count >= 3 and not _fallback_active:
                    _fallback_active = True
                    _fallback_started_at = now
                    logger.error(
                        "Wallex failed 3 times. Switching primary fetcher to Bitpin for 2 mins."
                    )
                logger.info("Sleeping 2 seconds before falling back to next source...")
                await asyncio.sleep(2.0)
            continue

    logger.error("All market sources failed for this poll cycle")
    return None


async def get_latest_snapshot() -> Optional[dict]:
    """Return the most recent market_snapshots row, or None if empty."""
    conn = get_db()
    cursor = await conn.execute(
        "SELECT id, timestamp, price, volume, source "
        "FROM market_snapshots ORDER BY timestamp DESC LIMIT 1"
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "timestamp": row[1],
        "price": row[2],
        "volume": row[3],
        "source": row[4],
    }


async def _find_closest_snapshot(target_timestamp: int) -> Optional[dict]:
    """Find the snapshot whose timestamp is closest to target_timestamp."""
    conn = get_db()
    cursor = await conn.execute(
        "SELECT id, timestamp, price, volume, source "
        "FROM market_snapshots "
        "ORDER BY ABS(timestamp - ?) LIMIT 1",
        (target_timestamp,),
    )
    row = await cursor.fetchone()
    await cursor.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "timestamp": row[1],
        "price": row[2],
        "volume": row[3],
        "source": row[4],
    }


async def get_volume_change(
    current: Optional[dict] = None, target_hours: float = 24.0
) -> Union[float, str]:
    """
    Compute rolling volume momentum as a percentage change vs `target_hours` ago.
    """
    if current is None:
        current = await get_latest_snapshot()
    if current is None:
        return "N/A"

    if current.get("volume") is None:
        return "N/A"

    target_timestamp = int(current["timestamp"] - target_hours * 3600)
    past = await _find_closest_snapshot(target_timestamp)
    if past is None:
        return "N/A"

    if abs(past["timestamp"] - target_timestamp) > TOLERANCE_SECONDS:
        return "N/A"

    if past["source"] != current["source"]:
        return "N/A"

    if past.get("volume") is None:
        return "N/A"

    past_volume = past["volume"]
    if past_volume == 0:
        return "N/A"

    current_volume = current["volume"]
    change_pct = ((current_volume - past_volume) / past_volume) * 100
    return round(change_pct, 2)


async def record_snapshot(
    price: float,
    volume: Optional[float],
    source: str,
    change_24h: Optional[float] = None,
) -> None:
    """Convenience wrapper used by the poller to persist a new snapshot."""
    from app.database import insert_snapshot

    await insert_snapshot(
        timestamp=int(time.time()),
        price=price,
        volume=volume,
        source=source,
        change_24h=change_24h,
    )
