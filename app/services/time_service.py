"""
Virtual Time Management Service for Testing & Simulation.
Manages global time offset to allow time-travel testing via Swagger/API.
"""
from datetime import datetime, timezone
import time

# متغیر سرتاسری انحراف زمانی به ثانیه
_TIME_OFFSET_SECONDS: float = 0.0


def get_current_timestamp() -> float:
    """
    زمان فعلی (Timestamp به ثانیه) را با احتساب انحراف زمانی مجازی برمی‌گرداند.
    """
    return time.time() + _TIME_OFFSET_SECONDS


def get_current_datetime() -> datetime:
    """
    شیء datetime زمان فعلی را با احتساب انحراف زمانی مجازی برمی‌گرداند.
    """
    return datetime.fromtimestamp(get_current_timestamp(), tz=timezone.utc)


def advance_time(minutes: float = 0.0, days: float = 0.0) -> float:
    """
    زمان مجازی سیستم را به میزان مشخصی به آینده می‌برد.
    """
    global _TIME_OFFSET_SECONDS
    added_seconds = (minutes * 60.0) + (days * 86400.0)
    _TIME_OFFSET_SECONDS += added_seconds
    return _TIME_OFFSET_SECONDS


def set_time_offset(offset_seconds: float) -> None:
    """
    مقدار انحراف زمانی را مستقیماً تنظیم می‌کند.
    """
    global _TIME_OFFSET_SECONDS
    _TIME_OFFSET_SECONDS = offset_seconds


def reset_time() -> None:
    """
    زمان را به ساعت واقعی سیستم بازمی‌گرداند.
    """
    global _TIME_OFFSET_SECONDS
    _TIME_OFFSET_SECONDS = 0.0


def get_time_status() -> dict:
    """
    خروجی وضعیت فعلی زمان واقعی و زمان مجازی را برمی‌گرداند.
    """
    real_now = datetime.fromtimestamp(time.time(), tz=timezone.utc)
    virtual_now = get_current_datetime()
    return {
        "real_time": real_now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "virtual_time": virtual_now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "offset_seconds": _TIME_OFFSET_SECONDS,
        "offset_minutes": round(_TIME_OFFSET_SECONDS / 60.0, 2),
        "offset_days": round(_TIME_OFFSET_SECONDS / 86400.0, 2),
    }