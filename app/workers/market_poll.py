"""
Background polling loops: market price ticks and RSS news checks.

Extracted from main.py. These are the two long-running asyncio.Task loops
that main.py's FastAPI lifespan spawns on startup and cancels on shutdown.
Behavior is unchanged from the original main.py implementation.
"""
import asyncio
import logging
import time

from telegram.ext import Application

from app import database
from app.bot.bot_state import poller_status
from app.config import settings
from app.services.alarm_service import evaluate_and_trigger_alarms
from app.services.market_service import get_market_data

logger = logging.getLogger(__name__)


async def market_poll_loop(bot_app: Application) -> None:
    """
    چرخه اصلی پولینگ بازارهای تتر (هر ۳ ثانیه یک‌بار).
    """
    logger.info(
        "Starting market polling loop. Poll interval: %d seconds.",
        settings.market_poll_interval
    )
    db_write_counter = 0

    while True:
        start_time = asyncio.get_event_loop().time()
        try:
            data = await get_market_data()
            if data is not None:
                current_price = data["price"]

                # بروزرسانی آنی حافظه موقت (رم) برای اعتبارسنجی‌ها
                poller_status.last_price = current_price
                poller_status.last_source = data["source"]
                poller_status.last_market_success = start_time
                poller_status.last_market_error = None

                # ارزیابی فوری و شلیک هشدارهای زنده مارکت در همین تیک جاری قیمت
                await evaluate_and_trigger_alarms(current_price, data["source"], bot_app)

                # کنترل فرکانس ذخیره‌سازی در دیتابیس (Throttling 15s)
                db_write_counter += 1
                if db_write_counter >= 5:
                    await database.insert_snapshot(
                        timestamp=int(time.time()),
                        price=current_price,
                        volume=data["volume"],
                        source=data["source"],
                        change_24h=data.get("change_24h"),
                    )
                    logger.info(
                        "Recorded snapshot to DB: %.2f IRT from %s (Throttled 15s)",
                        current_price, data["source"]
                    )
                    db_write_counter = 0
            else:
                poller_status.last_market_error = "No valid data received from any source"

        except Exception as e:
            logger.error("Error in market polling cycle: %s", e, exc_info=True)
            poller_status.last_market_error = str(e)

        elapsed = asyncio.get_event_loop().time() - start_time
        sleep_time = max(0.1, settings.market_poll_interval - elapsed)
        await asyncio.sleep(sleep_time)


async def news_poll_loop() -> None:
    """
    چرخه بررسی اخبار روز تتر و ذخیره در سیستم.
    """
    logger.info("Starting news polling loop. Poll interval: %d seconds.", settings.rss_poll_interval)
    while True:
        start_time = asyncio.get_event_loop().time()
        try:
            poller_status.last_news_success = start_time
            poller_status.last_news_error = None
        except Exception as e:
            logger.error("Error in news polling cycle: %s", e)
            poller_status.last_news_error = str(e)

        elapsed = asyncio.get_event_loop().time() - start_time
        sleep_time = max(1.0, settings.rss_poll_interval - elapsed)
        await asyncio.sleep(sleep_time)
