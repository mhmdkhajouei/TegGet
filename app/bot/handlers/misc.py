"""
Standalone command handlers that don't belong to a ConversationHandler.
"""
import asyncio
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from app import database
from app.bot.bot_state import poller_status
from app.bot.handlers.formatting import _format_age
from app.bot.handlers.keyboards import MAIN_MENU_KEYBOARD

logger = logging.getLogger(__name__)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    first_name = user.first_name if user else None

    context.user_data.pop("invalid_text_count", None)
    context.user_data.pop("invalid_command_count", None)

    await database.upsert_user(chat_id=chat.id, first_name=first_name)

    await update.message.reply_text(
        "به ردیاب تتر خوش اومدی 👋\n\n"
        "از دکمه‌های پایین صفحه استفاده کن:\n"
        "🔔 برای تنظیم هشدار قیمت\n"
        "💵 برای دیدن قیمت لحظه‌ای\n"
        "📰 برای بررسی اخبار\n"
        "👤 برای مدیریت پروفایل و هشدارها",
        reply_markup=MAIN_MENU_KEYBOARD,
    )


async def handle_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    ارسال ساده پیام جدید بدون دستکاری استیت‌ها یا پیغام‌های قبلی چت.
    """
    await update.message.reply_text(
        "قابلیت اخبار به‌زودی اضافه می‌شه. فعلاً از 💵 قیمت لحظه‌ای و 🔔 هشدار استفاده کن."
    )


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = time.time()
    lines = ["🩺 وضعیت سرویس‌های پس‌زمینه", "――――――――――――", ""]

    if poller_status.last_market_success is not None:
        age = _format_age(int(now - poller_status.last_market_success))
        lines.append(f"✅ بازار: آخرین دریافت موفق {age} پیش")
    elif poller_status.last_market_error is not None:
        lines.append(f"⚠️ بازار: آخرین تلاش ناموفق ({poller_status.last_market_error})")
    else:
        lines.append("⏳ بازار: هنوز داده‌ای دریافت نشده")

    if poller_status.last_news_success is not None:
        age = _format_age(int(now - poller_status.last_news_success))
        lines.append(f"✅ اخبار: آخرین بررسی موفق {age} پیش")
    elif poller_status.last_news_error is not None:
        lines.append(f"⚠️ اخبار: آخرین تلاش ناموفق ({poller_status.last_news_error})")
    else:
        lines.append("⏳ اخبار: هنوز بررسی نشده")

    await update.message.reply_text("\n".join(lines))


async def _delete_message_after_delay(chat_id: int, message_id: int, delay: float,
                                      context: ContextTypes.DEFAULT_TYPE) -> None:
    await asyncio.sleep(delay)
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except BadRequest:
        pass


async def handle_invalid_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_message_id = update.message.message_id

    context.application.create_task(
        _delete_message_after_delay(chat_id, user_message_id, 10.0, context)
    )

    invalid_count = context.user_data.get("invalid_text_count", 0)

    if invalid_count == 0:
        context.user_data["invalid_text_count"] = 1
        guide_text = (
            "🤔 مطمئن نیستم چی می‌خوای. از منوی زیر استفاده کن:\n\n"
            "🔔 تنظیم هشدار قیمت\n"
            "💵 دیدن قیمت لحظه‌ای\n"
            "📰 بررسی اخبار\n"
            "👤 مدیریت پروفایل و هشدارها"
        )
        await update.message.reply_text(guide_text)
    else:
        pass


async def handle_unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_message_id = update.message.message_id

    context.application.create_task(
        _delete_message_after_delay(chat_id, user_message_id, 10.0, context)
    )

    invalid_cmd_count = context.user_data.get("invalid_command_count", 0)

    if invalid_cmd_count == 0:
        context.user_data["invalid_command_count"] = 1
        guide_text = (
            "🤔 همچین دستوری ندارم. منظورت یکی از این‌ها بود？\n\n"
            "🔔  تنظیم هشدار قیمت\n"
            "💵  دیدن قیمت لحظه‌ای\n"
            "📰  بررسی اخبار\n"
            "👤  مدیریت پروفایل و هشدارها"
        )
        await update.message.reply_text(guide_text)
    else:
        pass