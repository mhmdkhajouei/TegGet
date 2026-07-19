"""
/price command, the inline refresh button, and the shared rate limiter.
"""
import asyncio
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from app import database
from app.bot.bot_state import poller_status
from app.bot.handlers.formatting import (
    _format_partial_value,
    _format_tehran_hhmm,
    _is_market_price_valid,
)
from app.bot.handlers.keyboards import _get_price_keyboard

logger = logging.getLogger(__name__)

REFRESH_COOLDOWN_TIME = 5.0
STALE_THRESHOLD_SECONDS = 60.0

PRICE_RATE_LIMIT = 20
PRICE_RATE_WINDOW = 60

_price_request_log: dict[int, list[float]] = {}


def _is_rate_limited(chat_id: int) -> bool:
    now = time.time()
    window_start = now - PRICE_RATE_WINDOW
    timestamps = _price_request_log.get(chat_id, [])

    timestamps = [t for t in timestamps if t > window_start]
    _price_request_log[chat_id] = timestamps

    if len(timestamps) >= PRICE_RATE_LIMIT:
        return True

    _price_request_log[chat_id].append(now)
    return False


def _format_change(c: float) -> str:
    if c > 0:
        return f"🟢 +{c:.2f}٪"
    elif c < 0:
        return f"🔴 {c:.2f}٪"
    return "⚪️ بدون تغییر"


def _format_volume(v: float, source: str | None) -> str:
    if v == 0:
        return "—"
    if source == "wallex":
        return f"{v:,.0f} تومان"
    return f"{v:,.0f}"


async def _build_price_card(now: float) -> tuple[str | None, str]:
    try:
        snapshots = await database.get_latest_snapshots(limit=5)
    except Exception as e:
        logger.critical("SYSTEM PARALYSIS: Database query failed! Error: %s", e)
        return None, "db_error"

    if not snapshots:
        logger.critical("SYSTEM PARALYSIS: Database is empty!")
        return None, "empty"

    current = None
    first_snapshot_invalid = not _is_market_price_valid(snapshots[0]["price"])
    for snap in snapshots:
        if _is_market_price_valid(snap["price"]):
            current = snap
            break

    if current is None:
        logger.critical("SYSTEM PARALYSIS: All recent snapshots in database are invalid!")
        return None, "all_invalid"

    stale_line = ""
    is_stale = ((now - current["timestamp"]) > STALE_THRESHOLD_SECONDS) or first_snapshot_invalid
    if is_stale:
        stale_line = "⚠️ قیمت قدیمی‌ست٬ به‌روزرسانی جدید ناموفق بود\n\n"

    price_label = f"{current['price']:,.0f}"
    change_label = _format_partial_value(current["change_24h"], _format_change)
    volume_label = _format_partial_value(
        current["volume"], lambda v: _format_volume(v, current.get("source"))
    )
    source_label = _format_partial_value(current.get("source"))
    time_label = _format_partial_value(current.get("timestamp"), _format_tehran_hhmm)

    text = (
        f"{stale_line}"
        "💵 نرخ لحظه‌ای تتر\n"
        "――――――――――――\n\n"
        f"💰 قیمت: {price_label} تومان\n"
        f"📊 تغییر ۲۴ ساعته قیمت: {change_label}\n"
        f"📦 حجم ۲۴ ساعته: {volume_label}\n"
        f"🌐 منبع: {source_label}\n"
        f"🕒 ساعت: {time_label} (به وقت تهران)"
    )
    return text, ""


_FETCH_FAILURE_TEXT = "❌ الان نتونستیم قیمت رو بگیریم.\nچند لحظه دیگه دوباره امتحان کن."
_RATE_LIMIT_TEXT = (
    "⚠️ کمی آروم‌تر! در یک دقیقه می‌توانی حداکثر ۲۰ بار قیمت را بررسی کنی.\n"
    "لطفاً چند لحظه صبر کن."
)


async def _render_price_logic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    منطق ساده رندر قیمت: همواره پیام جدید ارسال می‌کند و کاری با ادیت پیام‌های دیگر ندارد.
    """
    chat_id = update.effective_chat.id
    now = time.time()

    cooldown_until = context.user_data.get("price_cooldown_until", 0.0)
    if now < cooldown_until:
        return

    context.user_data["price_cooldown_until"] = now + REFRESH_COOLDOWN_TIME

    # حذف دکمه بروزرسانی از پیام قبلی قیمت (در صورت وجود) برای جلوگیری از کلیک‌های همزمان مکرر
    last_msg_id = context.user_data.get("last_price_message_id")
    if last_msg_id:
        try:
            await context.bot.edit_message_reply_markup(chat_id=chat_id, message_id=last_msg_id, reply_markup=None)
        except BadRequest:
            context.user_data.pop("last_price_message_id", None)

    current_msg = await update.message.reply_text("⏳ در حال دریافت قیمت لحظه‌ای تتر…")

    if _is_rate_limited(chat_id):
        await current_msg.edit_text(_RATE_LIMIT_TEXT)
        return

    text, _ = await _build_price_card(now)
    if text is None:
        await current_msg.edit_text(_FETCH_FAILURE_TEXT, reply_markup=_get_price_keyboard())
        return

    new_msg = await current_msg.edit_text(text, reply_markup=_get_price_keyboard())
    context.user_data["last_price_message_id"] = new_msg.message_id


async def handle_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("invalid_text_count", None)
    context.user_data.pop("invalid_command_count", None)
    await _render_price_logic(update, context)


async def handle_price_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    now = time.time()

    cooldown_until = context.user_data.get("price_cooldown_until", 0.0)

    if now < cooldown_until:
        if context.user_data.get("refresh_animation_running"):
            return
        context.user_data["refresh_animation_running"] = True

        try:
            while True:
                current_now = time.time()
                remaining = int(round(cooldown_until - current_now))
                if remaining <= 0:
                    break

                temp_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"⏳ {remaining} ثانیه دیگر صبر کنید...", callback_data="cooldown_waiting")]
                ])
                await query.edit_message_reply_markup(reply_markup=temp_keyboard)
                await asyncio.sleep(1.0)
        finally:
            await query.edit_message_reply_markup(reply_markup=_get_price_keyboard())
            context.user_data["refresh_animation_running"] = False
        return

    context.user_data["price_cooldown_until"] = now + REFRESH_COOLDOWN_TIME

    context.user_data.pop("invalid_command_count", None)
    context.user_data.pop("invalid_text_count", None)

    if _is_rate_limited(chat_id):
        await query.edit_message_text(_RATE_LIMIT_TEXT)
        return

    text, _ = await _build_price_card(now)
    if text is None:
        await query.edit_message_text(_FETCH_FAILURE_TEXT, reply_markup=_get_price_keyboard())
        return

    try:
        new_msg = await query.edit_message_text(text, reply_markup=_get_price_keyboard())
        context.user_data["last_price_message_id"] = new_msg.message_id
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass
        else:
            raise e