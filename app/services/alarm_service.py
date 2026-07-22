"""
Alarm evaluation and trigger engine (HTML Parse Mode, Virtual Time & Auto Re-arm Integration).
"""
import logging
from datetime import datetime, timezone

from telegram.ext import ExtBot
from app import database
from app.services import time_service

logger = logging.getLogger(__name__)

_HYSTERESIS_BAND = 0.003  # 0.3% باند اطمینان برای Re-arm شدن
_EVERY_TIME_COOLDOWN_SECONDS = 180

_CONDITION_LABELS = {
    "above": "📈 بالاتر از",
    "below": "📉 پایین‌تر از",
    "percentage_up": "🚀 افزایش بیش از",
    "percentage_down": "🔻 کاهش بیش از",
}


async def evaluate_and_trigger_alarms(
    current_price: float, source: str, bot_token: str
) -> None:
    try:
        active_alarms = await database.get_active_alarms()
        if not active_alarms:
            return

        now = time_service.get_current_timestamp()
        current_virtual_datetime = time_service.get_current_datetime()
        current_date_str = current_virtual_datetime.strftime("%Y-%m-%d")

        for alarm in active_alarms:
            alarm_id = alarm["id"]
            chat_id = alarm["chat_id"]
            target_price = alarm["target_price"]
            condition = alarm["condition"]
            frequency = alarm["frequency"]
            last_triggered = alarm["last_triggered_at"] or 0
            is_armed = alarm["is_armed"]

            # ---------------------------------------------------------------------------
            # ۱. منطق Re-arm (مسلح‌سازی مجدد در صورت خروج قیمت از محدوده target)
            # ---------------------------------------------------------------------------
            if not is_armed:
                hysteresis_val = target_price * _HYSTERESIS_BAND
                should_rearm = False

                if condition in ("above", "percentage_up") and current_price < (target_price - hysteresis_val):
                    should_rearm = True
                elif condition in ("below", "percentage_down") and current_price > (target_price + hysteresis_val):
                    should_rearm = True

                if should_rearm:
                    # مسلح کردن مجدد هشدار در دیتابیس
                    await database.update_alarm_armed_status(alarm_id, is_armed=1)
                    logger.info("🔄 Alarm %s RE-ARMED (Price exited target threshold)", alarm_id)
                    is_armed = 1
                else:
                    # اگر هنوز قیمت از محدوده خارج نشده، شلیک نکن
                    continue

            # ---------------------------------------------------------------------------
            # ۲. بررسی شرط قیمت
            # ---------------------------------------------------------------------------
            is_condition_met = False

            if condition in ("above", "percentage_up"):
                is_condition_met = current_price >= target_price
            elif condition in ("below", "percentage_down"):
                is_condition_met = current_price <= target_price

            if not is_condition_met:
                continue

            # ---------------------------------------------------------------------------
            # ۳. بررسی Cooldown و شرط Daily (با تاریخ مجازی)
            # ---------------------------------------------------------------------------
            # بررسی کول‌داون ۳ دقیقه‌ای برای حالت every_time
            if frequency == "every_time":
                if now - last_triggered < _EVERY_TIME_COOLDOWN_SECONDS:
                    continue

            # بررسی عدم شلیک مجدد در همان روز تقویمی برای حالت daily
            if frequency == "daily" and last_triggered > 0:
                last_triggered_dt = datetime.fromtimestamp(
                    last_triggered, tz=timezone.utc
                )
                last_date_str = last_triggered_dt.strftime("%Y-%m-%d")

                logger.debug(
                    "🔍 [DEBUG DAILY] Alarm %s | Last Date: %s | Virtual Date: %s",
                    alarm_id,
                    last_date_str,
                    current_date_str,
                )

                # اگر تاریخ شلیک قبلی بزرگتر یا مساوی تاریخ مجازی جاری باشد -> بلاک
                if last_date_str >= current_date_str:
                    logger.debug(
                        "⛔ [BLOCKED DAILY] Alarm %s already triggered today (%s)",
                        alarm_id,
                        last_date_str,
                    )
                    continue

            # ---------------------------------------------------------------------------
            # ۴. ساخت متن HTML استاندارد
            # ---------------------------------------------------------------------------
            cond_str = _CONDITION_LABELS.get(condition, condition)

            message_text = (
                f"🔔 <b>هشدار قیمت تتر محقق شد!</b>\n"
                f"----------------------------------------\n\n"
                f"🎯 شرط هدف: {cond_str} {target_price:,.0f} تومان\n"
                f"💰 قیمت فعلی بازار: {current_price:,.0f} تومان\n"
                f"🌐 منبع قیمت: {source}\n\n"
            )

            if frequency == "once":
                message_text += "💡 این هشدار یک‌بار مصرف بود و اکنون غیرفعال گردید."
            else:
                message_text += f"🔁 تناوب هشدار: {frequency == 'daily' and 'روزی یک‌بار' or 'هر بار (با رعایت نوسان)'}"

            # ---------------------------------------------------------------------------
            # ۵. ارسال به تلگرام با HTML و به‌روزرسانی دیتابیس
            # ---------------------------------------------------------------------------
            try:
                local_bot = ExtBot(token=bot_token)
                async with local_bot:
                    await local_bot.send_message(
                        chat_id=chat_id,
                        text=message_text,
                        parse_mode="HTML"
                    )
                logger.info("SUCCESS: Telegram alert sent to chat_id=%s for alarm %s", chat_id, alarm_id)

                if frequency == "once":
                    await database.deactivate_alarm(alarm_id)
                else:
                    # ثبت timestamp مجازی فعلی و غیرمسلح کردن تا زمان خروج دوباره قیمت از محدوده
                    await database.update_alarm_triggered_at(alarm_id, int(now), is_armed=0)

            except Exception as tg_err:
                logger.error("FAILED to send Telegram message for alarm %s: %s", alarm_id, tg_err, exc_info=True)

    except Exception as e:
        logger.error("Error in alarm evaluation loop: %s", e, exc_info=True)