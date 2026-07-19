"""
In-memory diagnostic state for the background pollers.

This is intentionally NOT persisted to the database -- it's a liveness
signal for the /status command, and resetting on restart is correct
behavior (a fresh process hasn't polled anything yet, which is accurate
information, not a bug).

Lives in its own module (rather than inside main.py or handlers.py) so
both can import it without creating a circular import: main.py owns the
poll loops that write to it, handlers.py only reads from it.
"""
from dataclasses import dataclass


@dataclass
class PollerStatus:
    last_market_success: float | None = None
    last_market_error: str | None = None
    last_news_success: float | None = None
    last_news_error: str | None = None

    # ذخیره آخرین اطلاعات قیمت ۳ ثانیه‌ای بازار برای اعتبارسنجی‌های آنی
    last_price: float | None = None
    last_source: str | None = None


poller_status = PollerStatus()
