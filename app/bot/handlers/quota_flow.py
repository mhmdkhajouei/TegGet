"""
The "you've hit your 3-alarm limit" sub-flow.

Triggered from within the alarm-creation ConversationHandler (both at
entry, and if the limit is reached mid-flow between the number and
frequency steps) when a user is at MAX_ACTIVE_ALARMS. Lets them view and
delete existing alarms to free up a slot without leaving the alarm
conversation's state machine -- these callbacks are registered inside
alarm_flow.py's WAITING_CONDITION state.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from app import database
from app.bot.handlers.formatting import _CONDITION_LABELS, _FREQUENCY_LABELS
from app.bot.handlers.keyboards import _condition_option_rows, _with_cancel_footer
from app.bot.handlers.states import WAITING_CONDITION
from app.bot.handlers.timeout_job import _clear_alarm_timeout_job, _handle_alarm_timeout_trigger


async def handle_quota_alarms_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    رندر کردن لیست هشدارها برای کاربری که سقفش پر شده؛ فقط با دکمه منوی اصلی
    """
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    alarms = await database.get_user_alarms(chat_id)

    if not alarms:
        # اگر هیچ هشداری نبود، مستقیماً پیام ظرفیت خالی را نمایش بدهد
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔔 ساخت هشدار جدید", callback_data="quota:start_new_alarm")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="quota:main_menu")]
        ])
        await query.edit_message_text(
            "حالا یه جای خالی داری، بسازیم؟",
            reply_markup=keyboard
        )
        return WAITING_CONDITION

    lines = ["🔔 هشدارهای شما برای مدیریت و حذف:", "――――――――――――", ""]
    rows = []
    for i, alarm in enumerate(alarms, start=1):
        lines.append(
            f"{i}. {_CONDITION_LABELS.get(alarm['condition'], alarm['condition'])}\n"
            f"   🎯 {alarm['target_price']:,.0f} تومان   |   🔁 "
            f"{_FREQUENCY_LABELS.get(alarm['frequency'], alarm['frequency'])}\n"
        )
        rows.append([InlineKeyboardButton(f"❌ حذف هشدار {i}", callback_data=f"quota_del:{alarm['id']}")])

    # استفاده از کالبک اختصاصی quota:main_menu به جای کالبک سراسری تلگرام
    rows.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="quota:main_menu")])

    text = "\n".join(lines)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows))
    return WAITING_CONDITION


async def handle_quota_delete_alarm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    حذف هشدار و ادیت درجا پیام به متن و دکمه ساخت هشدار جدید
    """
    query = update.callback_query
    chat_id = update.effective_chat.id
    alarm_id = int(query.data.split(":", 1)[1])

    await database.delete_alarm(alarm_id, chat_id)
    await query.answer("هشدار حذف شد.")

    # ادیت درجا پیام به ساختار جدید پس از باز شدن ظرفیت
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔔 ساخت هشدار جدید", callback_data="quota:start_new_alarm")],
        [InlineKeyboardButton("🏠 منوی اصلی", callback_data="quota:main_menu")]
    ])

    await query.edit_message_text(
        "هشدار حذف شد ✅ حالا یه جای خالی داری، بسازیم؟",
        reply_markup=keyboard
    )
    return WAITING_CONDITION


async def handle_quota_start_new_alarm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    ادیت درجا پیام و باز کردن فلو انتخاب شرط هشدار جدید
    """
    query = update.callback_query
    await query.answer()
    chat_id = update.effective_chat.id

    _clear_alarm_timeout_job(chat_id, context)

    keyboard = _with_cancel_footer(_condition_option_rows())
    await query.edit_message_text(
        "🔔 هشدار جدید\n"
        "――――――――――――\n\n"
        "اول نوع شرط هشدار را انتخاب کن:",
        reply_markup=keyboard,
    )

    if context.application.job_queue:
        context.application.job_queue.run_once(
            _handle_alarm_timeout_trigger,
            when=900,
            name=f"alarm_timeout_{chat_id}",
            data={"chat_id": chat_id, "message_id": query.message.message_id}
        )
    return WAITING_CONDITION


async def handle_quota_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    ادیت درجا پیام منوی سقف به پیام راهنمای منوی اصلی ربات و بستن فلو
    """
    query = update.callback_query
    await query.answer()

    _clear_alarm_timeout_job(update.effective_chat.id, context)
    context.user_data.clear()

    await query.edit_message_text(
        "🏠 منوی اصلی\n\nاز دکمه‌های زیر برای ادامه استفاده کن."
    )
    return ConversationHandler.END
