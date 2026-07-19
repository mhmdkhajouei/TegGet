"""
Profile & Management Hub (v1.1) ConversationHandler.

Lets a user view/edit/delete their existing alarms and toggle news source
subscriptions, all as nested states of one conversation. The percentage
condition-edit path reads live price from poller_status (falling back to
the latest DB snapshot) to recompute a target price, same as the
alarm-creation flow.
"""
import re
import warnings

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.warnings import PTBUserWarning

from app import database
from app.bot.bot_state import poller_status
from app.bot.handlers.formatting import _CONDITION_LABELS, _FREQUENCY_LABELS, _parse_number, _validate_target_price
from app.bot.handlers.keyboards import BTN_PROFILE, _MENU_BUTTON_TEXTS, _profile_main_menu, _with_footer
from app.bot.handlers.nav import handle_global_interrupt, handle_nav_main_menu
from app.bot.handlers.states import (
    PROFILE_ALARMS,
    PROFILE_EDIT_ALARM,
    PROFILE_EDIT_CONDITION,
    PROFILE_EDIT_FREQUENCY,
    PROFILE_EDIT_PRICE,
    PROFILE_MAIN,
    PROFILE_NEWS,
)

_MENU_BUTTON_REGEX = "^(" + "|".join(re.escape(t) for t in _MENU_BUTTON_TEXTS) + ")$"


async def _render_alarms_menu(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    alarms = await database.get_user_alarms(chat_id)

    if not alarms:
        text = "هیچ هشداری ندارید."
        keyboard = _with_footer([], back_callback="profile:back_main")
        return text, keyboard

    lines = ["🔔 هشدارهای شما", "――――――――――――", ""]
    rows = []
    for i, alarm in enumerate(alarms, start=1):
        status_label = "🟢 فعال" if alarm["is_active"] else "⚪️ غیرفعال"
        lines.append(
            f"{i}. {_CONDITION_LABELS.get(alarm['condition'], alarm['condition'])}\n"
            f"   {status_label}\n"
            f"   🎯 {alarm['target_price']:,.0f} تومان   |   🔁 "
            f"{_FREQUENCY_LABELS.get(alarm['frequency'], alarm['frequency'])}\n"
        )
        rows.append([
            InlineKeyboardButton("✏️ ویرایش", callback_data=f"edit_alarm:{alarm['id']}"),
            InlineKeyboardButton("❌ حذف", callback_data=f"del_alarm:{alarm['id']}"),
        ])

    text = "\n".join(lines)
    keyboard = _with_footer(rows, back_callback="profile:back_main")
    return text, keyboard


async def _render_news_menu(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    from app.config import settings

    subscribed = set(await database.get_user_news_sources(chat_id))

    rows = []
    for rss_source in settings.rss_source_priority:
        name = rss_source["name"]
        icon = "✅" if name in subscribed else "⬛️"
        rows.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"news_toggle:{name}")])

    text = "📰 مدیریت اخبار\n――――――――――――\n\nروی هر منبع بزن تا فعال یا غیرفعالش کنی:"
    keyboard = _with_footer(rows, back_callback="profile:back_main")
    return text, keyboard


def _render_edit_alarm_field_menu(alarm: dict) -> tuple[str, InlineKeyboardMarkup]:
    rows = [[
        InlineKeyboardButton("💵 قیمت", callback_data="edit_field:price"),
        InlineKeyboardButton("⚙️ شرط", callback_data="edit_field:condition"),
        InlineKeyboardButton("🔄 تکرار", callback_data="edit_field:frequency"),
    ]]
    text = (
        "کدوم بخش این هشدار رو می‌خوای ویرایش کنی؟\n――――――――――――\n\n"
        f"🎯 قیمت هدف فعلی: {alarm['target_price']:,.0f} تومان\n"
        f"⚙️ شرط فعلی: {_CONDITION_LABELS.get(alarm['condition'], alarm['condition'])}\n"
        f"🔁 تناوب فعلی: {_FREQUENCY_LABELS.get(alarm['frequency'], alarm['frequency'])}"
    )
    keyboard = _with_footer(rows, back_callback="profile:back_alarms")
    return text, keyboard


async def handle_profile_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("profile_edit_alarm_id", None)
    context.user_data.pop("profile_edit_mode", None)
    context.user_data.pop("profile_pending_condition", None)

    # اگر آپدیت از یک پیام متنی دکمه ریپلای آمده و پیام لایو قبلی از قبل ادیت شده است،
    # نیازی به ارسال پیام متنی مجدد نیست و فقط استیت را تغییر می‌دهیم.
    if update.message and context.user_data.get("alarm_message_id"):
        return PROFILE_MAIN

    # حالت عادی (اگر کاربر در چت خالی کامند /profile زده باشد)
    await update.message.reply_text(
        "⚙️ مرکز مدیریت\n――――――――――――\n\nچه چیزی رو می‌خوای مدیریت کنی؟",
        reply_markup=_profile_main_menu(),
    )
    return PROFILE_MAIN


async def handle_profile_goto_alarms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    text, keyboard = await _render_alarms_menu(chat_id)
    await query.edit_message_text(text, reply_markup=keyboard)
    return PROFILE_ALARMS


async def handle_profile_show_alarms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    text, keyboard = await _render_alarms_menu(chat_id)
    await query.edit_message_text(text, reply_markup=keyboard)
    return PROFILE_ALARMS


async def handle_profile_show_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    text, keyboard = await _render_news_menu(chat_id)
    await query.edit_message_text(text, reply_markup=keyboard)
    return PROFILE_NEWS


async def handle_profile_back_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "⚙️ مرکز مدیریت\n――――――――――――\n\nچه چیزی رو می‌خوای مدیریت کنی؟",
        reply_markup=_profile_main_menu(),
    )
    return PROFILE_MAIN


async def handle_profile_toggle_news(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    source = query.data.split(":", 1)[1]

    await database.toggle_news_source(chat_id, source)

    text, keyboard = await _render_news_menu(chat_id)
    await query.edit_message_text(text, reply_markup=keyboard)
    return PROFILE_NEWS


async def handle_profile_delete_alarm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    alarm_id = int(query.data.split(":", 1)[1])

    await database.delete_alarm(alarm_id, chat_id)

    text, keyboard = await _render_alarms_menu(chat_id)
    await query.edit_message_text(text, reply_markup=keyboard)
    return PROFILE_ALARMS


async def handle_profile_edit_alarm_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    alarm_id = int(query.data.split(":", 1)[1])

    alarm = await database.get_alarm_by_id(alarm_id, chat_id)
    if alarm is None:
        await query.answer("این هشدار دیگر وجود ندارد.", show_alert=True)
        text, keyboard = await _render_alarms_menu(chat_id)
        await query.edit_message_text(text, reply_markup=keyboard)
        return PROFILE_ALARMS

    context.user_data["profile_edit_alarm_id"] = alarm_id

    text, keyboard = _render_edit_alarm_field_menu(alarm)
    await query.edit_message_text(text, reply_markup=keyboard)
    return PROFILE_EDIT_ALARM


async def handle_profile_back_to_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    alarm_id = context.user_data.get("profile_edit_alarm_id")
    chat_id = update.effective_chat.id

    if alarm_id is None:
        await query.edit_message_text("اطلاعات ویرایش از دست رفت. دوباره با /profile شروع کن.")
        return ConversationHandler.END

    alarm = await database.get_alarm_by_id(alarm_id, chat_id)
    if alarm is None:
        await query.edit_message_text("این هشدار دیگر وجود ندارد.")
        context.user_data.pop("profile_edit_alarm_id", None)
        return ConversationHandler.END

    context.user_data.pop("profile_edit_mode", None)
    context.user_data.pop("profile_pending_condition", None)

    text, keyboard = _render_edit_alarm_field_menu(alarm)
    await query.edit_message_text(text, reply_markup=keyboard)
    return PROFILE_EDIT_ALARM


async def handle_profile_back_alarms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    context.user_data.pop("profile_edit_alarm_id", None)
    context.user_data.pop("profile_edit_mode", None)
    context.user_data.pop("profile_pending_condition", None)

    chat_id = update.effective_chat.id
    text, keyboard = await _render_alarms_menu(chat_id)
    await query.edit_message_text(text, reply_markup=keyboard)
    return PROFILE_ALARMS


async def handle_profile_edit_field_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    context.user_data["profile_edit_mode"] = "price"

    keyboard = _with_footer([], back_callback="profile:back_edit_menu")
    await query.edit_message_text(
        "یک عدد برای قیمت هدف جدید وارد کن (مثلاً ۶۰۰۰۰):",
        reply_markup=keyboard,
    )
    return PROFILE_EDIT_PRICE


async def handle_profile_edit_field_condition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    keyboard = _with_footer(
        [
            [InlineKeyboardButton("📈 بالاتر از این قیمت",     callback_data="edit_condition:above")],
            [InlineKeyboardButton("📉 پایین‌تر از این قیمت",   callback_data="edit_condition:below")],
            [InlineKeyboardButton("🚀 افزایش بیش از این درصد", callback_data="edit_condition:percentage_up")],
            [InlineKeyboardButton("🔻 کاهش بیش از این درصد",  callback_data="edit_condition:percentage_down")],
        ],
        back_callback="profile:back_edit_menu",
    )

    await query.edit_message_text(
        "شرط جدید رو انتخاب کن:",
        reply_markup=keyboard,
    )
    return PROFILE_EDIT_CONDITION


async def handle_profile_edit_field_frequency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    keyboard = _with_footer(
        [
            [InlineKeyboardButton("1️⃣ فقط یک‌بار", callback_data="edit_frequency:once")],
            [InlineKeyboardButton("📅 روزی یک‌بار", callback_data="edit_frequency:daily")],
            [InlineKeyboardButton("🔁 هر بار",       callback_data="edit_frequency:every_time")],
        ],
        back_callback="profile:back_edit_menu",
    )

    await query.edit_message_text(
        "تناوب جدید رو انتخاب کن:",
        reply_markup=keyboard,
    )
    return PROFILE_EDIT_FREQUENCY


async def handle_profile_edit_price_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    alarm_id = context.user_data.get("profile_edit_alarm_id")
    chat_id = update.effective_chat.id

    if alarm_id is None:
        await update.message.reply_text("اطلاعات ویرایش از دست رفت. دوباره با /profile شروع کن.")
        return ConversationHandler.END

    alarm = await database.get_alarm_by_id(alarm_id, chat_id)
    if alarm is None:
        await update.message.reply_text("این هشدار دیگر وجود ندارد.")
        context.user_data.pop("profile_edit_alarm_id", None)
        context.user_data.pop("profile_edit_mode", None)
        context.user_data.pop("profile_pending_condition", None)
        return ConversationHandler.END

    value = _parse_number(update.message.text or "")
    if value is None:
        keyboard = _with_footer([], back_callback="profile:back_edit_menu")
        await update.message.reply_text(
            "متوجه نشدم 🤔 لطفاً فقط یک عدد معتبر وارد کن:",
            reply_markup=keyboard,
        )
        return PROFILE_EDIT_PRICE

    mode = context.user_data.get("profile_edit_mode")

    # --- ۱. اعتبارسنجی قیمت هدف هنگام ویرایش قیمت عادی ---
    if mode == "price":
        is_valid, err_msg = _validate_target_price(alarm["condition"], value)
        if not is_valid:
            keyboard = _with_footer([], back_callback="profile:back_edit_menu")
            await update.message.reply_text(err_msg, reply_markup=keyboard)
            return PROFILE_EDIT_PRICE

    # --- ۲. محاسبه تغییر شرط به حالت درصد بر اساس قیمت رم ---
    if mode == "condition_percentage":
        current_price = poller_status.last_price
        if current_price is None:
            snapshot = await database.get_latest_snapshot()
            current_price = snapshot["price"] if snapshot else None

        if current_price is None:
            await update.message.reply_text(
                "هنوز داده‌ای از بازار دریافت نشده. چند لحظه صبر کن و دوباره امتحان کن."
            )
            return ConversationHandler.END

        pct = value / 100.0
        condition = context.user_data.get("profile_pending_condition")

        if condition == "percentage_up":
            target_price = current_price * (1 + pct)
        else:
            target_price = current_price * (1 - pct)

        await database.update_alarm_condition(alarm_id, chat_id, condition)
        await database.update_alarm_target_price(alarm_id, chat_id, target_price)

        success_text = (
            "✅ شرط و قیمت هدف به‌روزرسانی شد.\n――――――――――――\n\n"
            f"💰 قیمت فعلی: {current_price:,.0f} تومان\n"
            f"📐 درصد: {value:g}٪\n"
            f"🎯 قیمت هدف جدید: {target_price:,.0f} تومان"
        )
    else:
        await database.update_alarm_target_price(alarm_id, chat_id, value)
        success_text = f"✅ قیمت هدف جدید ثبت شد: {value:,.0f} تومان"

    context.user_data.pop("profile_edit_alarm_id", None)
    context.user_data.pop("profile_edit_mode", None)
    context.user_data.pop("profile_pending_condition", None)

    await update.message.reply_text(success_text)

    text, keyboard = await _render_alarms_menu(chat_id)
    await update.message.reply_text(text, reply_markup=keyboard)
    return PROFILE_ALARMS


async def handle_profile_edit_condition_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    alarm_id = context.user_data.get("profile_edit_alarm_id")
    chat_id = update.effective_chat.id
    condition = query.data.split(":", 1)[1]

    if alarm_id is None:
        await query.edit_message_text("اطلاعات ویرایش از دست رفت. دوباره با /profile شروع کن.")
        return ConversationHandler.END

    alarm = await database.get_alarm_by_id(alarm_id, chat_id)
    if alarm is None:
        await query.edit_message_text("این هشدار دیگر وجود ندارد.")
        context.user_data.pop("profile_edit_alarm_id", None)
        return ConversationHandler.END

    if condition in ("above", "below"):
        await database.update_alarm_condition(alarm_id, chat_id, condition)
        context.user_data.pop("profile_edit_alarm_id", None)
        context.user_data.pop("profile_edit_mode", None)
        context.user_data.pop("profile_pending_condition", None)

        await query.edit_message_text(
            f"✅ شرط به‌روزرسانی شد: {_CONDITION_LABELS[condition]}"
        )

        text, keyboard = await _render_alarms_menu(chat_id)
        await query.message.reply_text(text, reply_markup=keyboard)
        return PROFILE_ALARMS

    context.user_data["profile_edit_mode"] = "condition_percentage"
    context.user_data["profile_pending_condition"] = condition

    keyboard = _with_footer([], back_callback="profile:back_edit_menu")
    await query.edit_message_text(
        f"شرط انتخابی: {_CONDITION_LABELS[condition]}\n\n"
        "یک عدد درصد وارد کن (مثلاً ۲.۵):",
        reply_markup=keyboard,
    )
    return PROFILE_EDIT_PRICE


async def handle_profile_edit_frequency_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    alarm_id = context.user_data.get("profile_edit_alarm_id")
    chat_id = update.effective_chat.id
    frequency = query.data.split(":", 1)[1]

    if alarm_id is None:
        await query.edit_message_text("اطلاعات ویرایش از دست رفت. دوباره با /profile شروع کن.")
        return ConversationHandler.END

    alarm = await database.get_alarm_by_id(alarm_id, chat_id)
    if alarm is None:
        await query.edit_message_text("این هشدار دیگر وجود ندارد.")
        context.user_data.pop("profile_edit_alarm_id", None)
        return ConversationHandler.END

    await database.update_alarm_frequency(alarm_id, chat_id, frequency)
    context.user_data.pop("profile_edit_alarm_id", None)
    context.user_data.pop("profile_edit_mode", None)
    context.user_data.pop("profile_pending_condition", None)

    await query.edit_message_text(
        f"✅ تناوب به‌روزرسانی شد: {_FREQUENCY_LABELS[frequency]}"
    )

    text, keyboard = await _render_alarms_menu(chat_id)
    await query.message.reply_text(text, reply_markup=keyboard)
    return PROFILE_ALARMS


async def handle_profile_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("profile_edit_alarm_id", None)
    context.user_data.pop("profile_edit_mode", None)
    context.user_data.pop("profile_pending_condition", None)

    await update.message.reply_text(
        "عملیات لغو شد. هر وقت خواستید با /profile دوباره شروع کنید."
    )
    return ConversationHandler.END


def build_profile_conversation_handler() -> ConversationHandler:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=PTBUserWarning)
        return ConversationHandler(
            entry_points=[
                CommandHandler("profile", handle_profile_entry),
                CallbackQueryHandler(handle_profile_goto_alarms, pattern=r"^go_to_profile_alarms$"),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_PROFILE)}$"), handle_profile_entry),
                # خط جدید: این کالبک به پروفایل اجازه می‌دهد تا وقتی کلیک روی پیام شیشه‌ای رخ می‌دهد،
                # مستقیماً استیت PROFILE_MAIN را بدون ارسال پیام متنی جدید فعال کند.
                CallbackQueryHandler(handle_profile_back_main, pattern=r"^profile:back_main$"),
            ],
            states={
                PROFILE_MAIN: [
                    CallbackQueryHandler(handle_profile_show_alarms, pattern=r"^profile:alarms$"),
                    CallbackQueryHandler(handle_profile_show_news, pattern=r"^profile:news$"),
                ],
                PROFILE_ALARMS: [
                    CallbackQueryHandler(handle_profile_edit_alarm_menu, pattern=r"^edit_alarm:\d+$"),
                    CallbackQueryHandler(handle_profile_delete_alarm, pattern=r"^del_alarm:\d+$"),
                    CallbackQueryHandler(handle_profile_back_main, pattern=r"^profile:back_main$"),
                ],
                PROFILE_NEWS: [
                    CallbackQueryHandler(handle_profile_toggle_news, pattern=r"^news_toggle:"),
                    CallbackQueryHandler(handle_profile_back_main, pattern=r"^profile:back_main$"),
                ],
                PROFILE_EDIT_ALARM: [
                    CallbackQueryHandler(handle_profile_edit_field_price, pattern=r"^edit_field:price$"),
                    CallbackQueryHandler(handle_profile_edit_field_condition, pattern=r"^edit_field:condition$"),
                    CallbackQueryHandler(handle_profile_edit_field_frequency, pattern=r"^edit_field:frequency$"),
                    CallbackQueryHandler(handle_profile_back_alarms, pattern=r"^profile:back_alarms$"),
                ],
                PROFILE_EDIT_PRICE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_profile_edit_price_receive),
                    CallbackQueryHandler(handle_profile_back_to_edit_menu, pattern=r"^profile:back_edit_menu$"),
                ],
                PROFILE_EDIT_CONDITION: [
                    CallbackQueryHandler(handle_profile_edit_condition_receive, pattern=r"^edit_condition:"),
                    CallbackQueryHandler(handle_profile_back_to_edit_menu, pattern=r"^profile:back_edit_menu$"),
                ],
                PROFILE_EDIT_FREQUENCY: [
                    CallbackQueryHandler(handle_profile_edit_frequency_receive, pattern=r"^edit_frequency:"),
                    CallbackQueryHandler(handle_profile_back_to_edit_menu, pattern=r"^profile:back_edit_menu$"),
                ],
            },
            fallbacks=[
                CommandHandler("cancel", handle_profile_cancel),
                CallbackQueryHandler(handle_nav_main_menu, pattern=r"^nav:main_menu$"),
                MessageHandler(filters.COMMAND | filters.Regex(_MENU_BUTTON_REGEX), handle_global_interrupt),
            ],
            per_message=False,
        )
