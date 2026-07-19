import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import database
from app.bot.bot_state import poller_status
from app.bot.handlers import build_application, process_update
from app.config import settings
from app.services.market_service import get_market_data

# تنظیم لایه لاگر پروژه
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# وهله‌سازی از ربات تلگرام بر اساس ساختار استاندارد PTB
bot_app = build_application()


async def _market_poll_loop():
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


async def _news_poll_loop():
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    مدیریت طول عمر برنامه FastAPI و شروع/خاتمه کار پروسه‌های پس‌زمینه با پولینگ مستقیم تلگرام.
    """
    # مقداردهی اولیه به کلاینت دیتابیس
    await database.init_db()

    # شروع به کار ربات در حالت Polling مخصوص تست لوکال
    await bot_app.initialize()

    # ۱. استارت موتور زمان‌بندی جاب‌کیو در استارت‌آپ
    if bot_app.job_queue:
        await bot_app.job_queue.start()

    await bot_app.updater.start_polling()
    await bot_app.start()
    logger.info("Telegram bot initialized and started via POLLING mode for local test.")

    # ثبت تسک‌های پس‌زمینه
    market_task = asyncio.create_task(_market_poll_loop())
    news_task = asyncio.create_task(_news_poll_loop())

    yield

    # فرآیند اتمام کار تسک‌ها هنگام خاموش شدن سرور
    market_task.cancel()
    news_task.cancel()
    try:
        await asyncio.gather(market_task, news_task, return_exceptions=True)
    except Exception as e:
        logger.error("Error while canceling background polling tasks: %s", e)

    # اتمام کار ربات تلگرام
    await bot_app.updater.stop()
    await bot_app.stop()

    # ۲. استاپ اصولی موتور زمان‌بندی جاب‌کیو در تِردون
    if bot_app.job_queue:
        await bot_app.job_queue.stop()

    await bot_app.shutdown()
    logger.info("Telegram bot stopped cleanly.")


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health_check():
    return {"status": "healthy"}
