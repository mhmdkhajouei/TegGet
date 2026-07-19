"""
Shared 15-minute expiry job for the alarm-creation flow's live message.

Both alarm_flow.py and quota_flow.py schedule/clear this job (quota_flow's
"start a new alarm from the quota screen" button re-enters the same flow
and needs to arm the same timeout), so it lives here to avoid a circular
import between the two.
"""
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app.bot.handlers.keyboards import NAV_MAIN_MENU_CALLBACK


def _clear_alarm_timeout_job(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    حذف تمام تایمرهای فعال پس‌زمینه انقضا برای یک چت خاص
    """
    if context.application.job_queue:
        job_name = f"alarm_timeout_{chat_id}"
        current_jobs = context.application.job_queue.get_jobs_by_name(job_name)
        for job in current_jobs:
            job.schedule_removal()


async def _handle_alarm_timeout_trigger(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    ماشه انقضای خودکار: ویرایش درجا یا ارسال پیام انقضا در صورت بن‌بست اینلاین
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    job = context.job
    chat_id = job.data["chat_id"]
    message_id = job.data["message_id"]

    # ۱. پاک‌سازی کامل حافظه موقت فرآیند
    context.application.user_data[chat_id].pop("alarm_condition", None)
    context.application.user_data[chat_id].pop("alarm_target_price", None)
    context.application.user_data[chat_id].pop("alarm_message_id", None)

    # ۲. فعال کردن پرچم دژ دفاعی
    context.application.user_data[chat_id]["alarm_flow_expired"] = True

    # اصلاح دکمه به منوی اصلی برای هدایت مستقیم کاربر
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منوی اصلی", callback_data=NAV_MAIN_MENU_CALLBACK)]])
    expire_text = "این فرایند منقضی شد. هر وقت خواستی از «🔔تنظیم هشدار قیمت» دوباره شروع کن."

    try:
        # تلاش برای ویرایش درجا
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=expire_text,
            reply_markup=keyboard
        )
    except BadRequest:
        # در صورت بن‌بست منوی شیشه‌ای در مرحله ۳: حذف پیام قبلی و ارسال پیام جدید انقضا
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except BadRequest:
            pass
        await context.bot.send_message(
            chat_id=chat_id,
            text=expire_text,
            reply_markup=keyboard
        )
