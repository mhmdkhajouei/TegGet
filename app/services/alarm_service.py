"""
Alarm evaluation and trigger engine.

Extracted from main.py (previously inline in the poll loop) to satisfy the
project's own layering rule: the market-poll loop should only drive the
tick, not contain the domain logic for what an alarm "firing" means. All
alarm math and Telegram dispatch for triggered alarms lives here.

Behavior is unchanged from the original main.py implementation — this is a
straight extraction, not a rewrite. See the class-level docstrings in
database.py for the schema this operates against (alarms table: condition,
frequency, is_armed, last_triggered_at).
"""
import logging
import time

from telegram.ext import Application

from app import database

logger = logging.getLogger(__name__)

# 0.3% neutral band used to require a genuine crossing (not noise) before
# an alarm is allowed to rearm after firing.
_HYSTERESIS_BAND = 0.003

# Cooldown windows per frequency mode, in seconds.
_EVERY_TIME_COOLDOWN_SECONDS = 180
_DAILY_COOLDOWN_SECONDS = 86400

_CONDITION_LABELS = {
    "above": "📈 بالاتر از",
    "below": "📉 پایین‌تر از",
    "percentage_up": "🚀 افزایش بیش از",
    "percentage_down": "🔻 کاهش بیش از",
}


async def evaluate_and_trigger_alarms(
    current_price: float, source: str, bot_app: Application
) -> None:
    """
    موتور اصلی ارزیابی و شلیک هشدارهای قیمت بر اساس سند فلو و تجربه کاربری.
    اجرا در هر تیک ۳ ثانیه‌ای بازار به‌صورت کاملاً ناهمگام و اتمیک.
    """
    try:
        active_alarms = await database.get_active_alarms()
        if not active_alarms:
            return

        now = time.time()

        for alarm in active_alarms:
            alarm_id = alarm["id"]
            chat_id = alarm["chat_id"]
            target_price = alarm["target_price"]
            condition = alarm["condition"]
            frequency = alarm["frequency"]
            last_triggered = alarm["last_triggered_at"]
            is_armed = alarm["is_armed"]

            # ---------------------------------------------------------------------------
            # لایه اول: ارزیابی وضعیت مسلح بودن (Arming & Reset) و باند خنثی (0.3% Hysteresis)
            # ---------------------------------------------------------------------------
            is_condition_met = False

            if condition == "above":
                is_condition_met = current_price >= target_price
                # اگر قیمت به زیر مرز برگشت، هشدار مجدداً مسلح (Rearm) می‌شود
                if not is_armed and current_price < target_price:
                    if frequency == "every_time":
                        # در حالت هر بار، بازگشت باید حتماً باند خنثی 0.3٪ را رد کند
                        if current_price <= target_price * (1 - _HYSTERESIS_BAND):
                            await database.update_alarm_armed_status(alarm_id, 1)
                            is_armed = 1
                    else:
                        await database.update_alarm_armed_status(alarm_id, 1)
                        is_armed = 1

            elif condition == "below":
                is_condition_met = current_price <= target_price
                # اگر قیمت به بالای مرز برگشت، هشدار مجدداً مسلح (Rearm) می‌شود
                if not is_armed and current_price > target_price:
                    if frequency == "every_time":
                        # در حالت هر بار، بازگشت باید حتماً باند خنثی 0.3٪ را رد کند
                        if current_price >= target_price * (1 + _HYSTERESIS_BAND):
                            await database.update_alarm_armed_status(alarm_id, 1)
                            is_armed = 1
                    else:
                        await database.update_alarm_armed_status(alarm_id, 1)
                        is_armed = 1

            elif condition == "percentage_up":
                is_condition_met = current_price >= target_price
                if not is_armed and current_price < target_price * (1 - _HYSTERESIS_BAND):
                    await database.update_alarm_armed_status(alarm_id, 1)
                    is_armed = 1

            elif condition == "percentage_down":
                is_condition_met = current_price <= target_price
                if not is_armed and current_price > target_price * (1 + _HYSTERESIS_BAND):
                    await database.update_alarm_armed_status(alarm_id, 1)
                    is_armed = 1

            # ---------------------------------------------------------------------------
            # لایه دوم: گیت‌های کنترلی تناوب و جلوگیری از اسپم (Frequency Control)
            # ---------------------------------------------------------------------------
            if not is_condition_met:
                continue

            # اگر شرط برقرار است ولی هشدار هنوز مسلح نشده (مربوط به منطق عبور بعدی یا باند خنثی)
            if not is_armed:
                continue

            # کنترل فلو اختصاصی برای حالت «هر بار» (کول‌داون ۳ دقیقه‌ای)
            if frequency == "every_time":
                if now - last_triggered < _EVERY_TIME_COOLDOWN_SECONDS:
                    continue

            # کنترل فلو اختصاصی برای حالت «روزانه» (گیت ۲۴ ساعته زمان‌محور)
            elif frequency == "daily":
                if now - last_triggered < _DAILY_COOLDOWN_SECONDS:
                    continue

            # ---------------------------------------------------------------------------
            # لایه سوم: شلیک اعلان، ارسال تلگرام و به‌روزرسانی اتمیک دیتابیس
            # ---------------------------------------------------------------------------

            # برچسب‌گذاری بصری شرط برای پیام تلگرام
            cond_str = _CONDITION_LABELS.get(condition, condition)

            # قالب‌بندی نهایی متن اعلان طبق مستندات تجربه کاربری
            message_text = (
                f"🔔 **هشدار قیمت تتر محقق شد!**\n"
                f"――――――――――――\n\n"
                f"🎯 شرط هدف: {cond_str} {target_price:,.0f} تومان\n"
                f"💰 قیمت فعلی بازار: {current_price:,.0f} تومان\n"
                f"🌐 منبع قیمت: {source}\n\n"
            )

            # اضافه کردن ضمیمه اختصاصی مصرف سهمیه به حالت «فقط یک‌بار»
            if frequency == "once":
                message_text += "💡 این هشدار یک‌بار مصرف بود و اکنون غیرفعال شد. جای خالی برای شما آزاد گردید."
            else:
                message_text += f"🔁 تناوب هشدار: {frequency == 'daily' and 'روزی یک‌بار' or 'هر بار (با رعایت نوسان)'}"

            try:
                # ارسال فراجای ناهمگام به API تلگرام
                await bot_app.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    parse_mode="Markdown"
                )
                logger.info("Successfully dispatched alarm alert to chat_id=%s", chat_id)

                # اعمال اکشن دیتابیس متناسب با نوع فرکانس
                if frequency == "once":
                    # تغییر وضعیت اتمیک به غیرفعال (فضای سقف ۳ تایی فوراً آزاد می‌شود)
                    await database.deactivate_alarm(alarm_id)
                else:
                    # ثبت زمان شلیک و سلب وضعیت مسلح (نامسلح کردن تا بازگشت بعدی قیمت)
                    await database.update_alarm_triggered_at(alarm_id, int(now))
                    await database.update_alarm_armed_status(alarm_id, 0)

            except Exception as tg_err:
                # مدیریت لبه بلاک شدن ربات توسط کاربر (تبدیل وضعیت به Paused در صورت توسعه آتی)
                logger.error("Failed to send Telegram alert for alarm %s: %s", alarm_id, tg_err)

    except Exception as e:
        logger.error("Error in alarm evaluation process loop: %s", e, exc_info=True)
