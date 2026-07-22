import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app import database
from app.bot.bot_state import poller_status
from app.bot.handlers import build_application
from app.config import settings
from app.services.market_service import get_market_data
from app.services.alarm_service import evaluate_and_trigger_alarms
from app.services.news_service import fetch_and_process_news
from app.services import time_service

# تنظیم لایه لاگر پروژه
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# وهله‌سازی از ربات تلگرام بر اساس ساختار استاندارد PTB
bot_app = build_application()


# ---------------------------------------------------------------------------
# Pydantic Schemas برای Swagger
# ---------------------------------------------------------------------------
class MockPriceRequest(BaseModel):
    price: float | None = None
    enable: bool = True


class AdvanceTimeRequest(BaseModel):
    minutes: float = Field(0.0, description="تعداد دقیقه‌هایی که زمان باید به جلو برود")
    days: float = Field(0.0, description="تعداد روزهایی که زمان باید به جلو برود")


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

                # پاس دادن توکن ربات به موتور ارزیابی برای شلیک مستقل و اتمیک
                await evaluate_and_trigger_alarms(current_price, data["source"], settings.telegram_bot_token)

                # کنترل فرکانس ذخیره‌سازی در دیتابیس (Throttling 15s)
                if not poller_status.mock_mode:
                    db_write_counter += 1
                    if db_write_counter >= 5:
                        await database.insert_snapshot(
                            timestamp=int(time_service.get_current_timestamp()),
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
    چرخه بررسی اخبار روز تتر و ذخیره در دیتابیس با موتور Scoring.
    """
    logger.info("Starting news polling loop. Poll interval: %d seconds.", settings.rss_poll_interval)
    while True:
        start_time = asyncio.get_event_loop().time()
        try:
            saved_count = await fetch_and_process_news()
            poller_status.last_news_success = start_time
            poller_status.last_news_error = None
            if saved_count > 0:
                logger.info("News poll completed: Saved %d new high-impact items.", saved_count)
        except Exception as e:
            logger.error("Error in news polling cycle: %s", e, exc_info=True)
            poller_status.last_news_error = str(e)

        elapsed = asyncio.get_event_loop().time() - start_time
        sleep_time = max(1.0, settings.rss_poll_interval - elapsed)
        await asyncio.sleep(sleep_time)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    مدیریت طول عمر برنامه FastAPI و شروع/خاتمه کار پروسه‌های پس‌زمینه با پولینگ مستقیم تلگرام.
    """
    await database.init_db()

    await bot_app.initialize()

    if bot_app.job_queue:
        logger.info("🚀 JobQueue initialized successfully. Starting the queue...")
        await bot_app.job_queue.start()
    else:
        logger.warning("❌ JobQueue is NOT available. Check your dependencies.")

    await bot_app.updater.start_polling(drop_pending_updates=True)
    await bot_app.start()
    logger.info("Telegram bot initialized and started via POLLING mode safely.")

    market_task = asyncio.create_task(_market_poll_loop())
    news_task = asyncio.create_task(_news_poll_loop())

    yield

    market_task.cancel()
    news_task.cancel()
    try:
        await asyncio.gather(market_task, news_task, return_exceptions=True)
    except Exception as e:
        logger.error("Error while canceling background polling tasks: %s", e)

    await bot_app.updater.stop()
    await bot_app.stop()

    if bot_app.job_queue:
        await bot_app.job_queue.stop()

    await bot_app.shutdown()
    logger.info("Telegram bot stopped cleanly.")


app = FastAPI(lifespan=lifespan)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}


# ---------------------------------------------------------------------------
# Mock Price Endpoints
# ---------------------------------------------------------------------------
@app.post("/admin/mock-price", tags=["Mock Price"])
async def set_mock_price(payload: MockPriceRequest):
    """
    اندپوئینت ادمین جهت تزریق دستی قیمت برای پرزنت زنده (Mock/Demo Mode)
    """
    if payload.enable and payload.price is not None:
        poller_status.mock_mode = True
        poller_status.mock_price = payload.price
        logger.info("DEMO MODE ACTIVE: Locked price to %.2f IRT", payload.price)
        return {
            "status": "success",
            "mock_mode": True,
            "message": f"حالت دمو فعال شد. قیمت بازار روی {payload.price:,.0f} تومان قفل گردید."
        }
    else:
        poller_status.mock_mode = False
        poller_status.mock_price = None
        logger.info("DEMO MODE DEACTIVATED: Restored live market source")
        return {
            "status": "success",
            "mock_mode": False,
            "message": "حالت دمو غیرفعال شد. ربات مجدداً به صرافی واقعی متصل گردید."
        }


# ---------------------------------------------------------------------------
# Mock Time Endpoints (برای تست‌های کول‌داون و روزانه)
# ---------------------------------------------------------------------------
@app.get("/admin/mock-time/status", tags=["Mock Time"])
async def get_mock_time_status():
    """
    مشاهده وضعیت فعلی زمان واقعی سرور، زمان مجازی ربات و مقدار Offset.
    """
    return time_service.get_time_status()


@app.post("/admin/mock-time/advance", tags=["Mock Time"])
async def advance_mock_time(payload: AdvanceTimeRequest):
    """
    جلو بردن زمان مجازی به میزان مشخص (دقیقه یا روز).
    مثال: جهت تست کول‌داون 3 دقیقه‌ای یا عبور از روز تقویمی.
    """
    time_service.advance_time(minutes=payload.minutes, days=payload.days)
    status = time_service.get_time_status()
    logger.info("MOCK TIME ADVANCED: Virtual time is now %s", status["virtual_time"])
    return {
        "status": "success",
        "message": f"زمان مجازی سیستم {payload.days} روز و {payload.minutes} دقیقه به جلو برده شد.",
        "time_status": status
    }


@app.post("/admin/mock-time/reset", tags=["Mock Time"])
async def reset_mock_time():
    """
    بازگرداندن زمان مجازی به ساعت واقعی سیستم.
    """
    time_service.reset_time()
    status = time_service.get_time_status()
    logger.info("MOCK TIME RESET to real system time.")
    return {
        "status": "success",
        "message": "زمان سیستم به ساعت واقعی سرور بازگردانده شد.",
        "time_status": status
    }