"""
Global navigation handlers: the shared "main menu" / "cancel flow"
callbacks, and the cross-cutting interrupt handler that lets a menu-button
tap or command break out of any nested ConversationHandler state cleanly.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from app.bot.handlers.keyboards import NAV_MAIN_MENU_CALLBACK
from app.bot.handlers.timeout_job import _clear_alarm_timeout_job


async def handle_flow_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    _clear_alarm_timeout_job(chat_id, context)
    context.user_data.clear()

    main_menu_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 منوی اصلی", callback_data=NAV_MAIN_MENU_CALLBACK)]])

    await query.edit_message_text(
        "❌ فرآیند تنظیم هشدار لغو شد.\n\n"
        "از دکمه‌های زیر یا منوی اصلی برای ادامه استفاده کن.",
        reply_markup=main_menu_markup
    )
    return ConversationHandler.END


async def handle_nav_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text(
        "🏠 منوی اصلی\n\nاز دکمه‌های زیر برای ادامه استفاده کن."
    )
    return ConversationHandler.END


async def handle_global_interrupt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    برخورد ساده با شکستن فلو: پاکسازی استیت فعلی و هدایت مستقیم کاربر
    به فلو یا دستور جدید بدون دستکاری یا ویرایش پیام‌های قبلی.
    """
    chat_id = update.effective_chat.id

    # ابطال تایمر انقضای فلو قبلی و پاکسازی وضعیت گفتگو
    _clear_alarm_timeout_job(chat_id, context)
    context.user_data.clear()

    # سپردن کنترل به تسک بعدی جهت ارسال پیام جدید
    context.application.create_task(context.application.process_update(update))
    return ConversationHandler.END