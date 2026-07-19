"""
Telegram bot application assembly.

The Application is built by `build_application()` but is NOT initialized
here -- initialize() and start() are called exactly once, during FastAPI's
lifespan startup in main.py, and stop()/shutdown() during lifespan
teardown.

This module only wires together the handler modules extracted from the
original monolithic handlers.py:
    - misc.py            /start, /status, /news stub, unknown command/message catch-alls
    - price.py            /price, refresh button, rate limiter
    - nav.py               global nav/cancel/interrupt handlers
    - alarm_flow.py        alarm-creation ConversationHandler
    - quota_flow.py        the "alarm limit reached" sub-flow (used inside alarm_flow)
    - profile_flow.py      the profile & management hub ConversationHandler
    - keyboards.py         keyboard builders
    - formatting.py        text/label/parsing helpers
    - states.py            shared ConversationHandler state constants
    - timeout_job.py        shared 15-minute alarm-flow expiry job

Handler registration order is preserved exactly as it was in the original
handlers.py, since PTB resolves overlapping filters/patterns in
registration order.
"""
import re

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from app.config import settings

from app.bot.handlers.keyboards import BTN_NEWS, BTN_PRICE, REFRESH_CALLBACK_DATA, _MENU_BUTTON_TEXTS
from app.bot.handlers.nav import handle_nav_main_menu
from app.bot.handlers.misc import (
    handle_news,
    handle_start,
    handle_status,
    handle_invalid_message,
    handle_unknown_command,
)
from app.bot.handlers.price import handle_price, handle_price_refresh
from app.bot.handlers.alarm_flow import build_alarm_conversation_handler
from app.bot.handlers.profile_flow import build_profile_conversation_handler

_MENU_BUTTON_REGEX = "^(" + "|".join(re.escape(t) for t in _MENU_BUTTON_TEXTS) + ")$"


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    # کتابخانه تلگرام خود به خود جاب‌کیو را می‌سازد و متصل می‌کند
    application = Application.builder().token(settings.telegram_bot_token).build()

    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("price", handle_price))
    application.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_PRICE)}$"), handle_price))
    application.add_handler(CommandHandler("news", handle_news))
    application.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_NEWS)}$"), handle_news))
    application.add_handler(CommandHandler("status", handle_status))
    application.add_handler(CallbackQueryHandler(handle_nav_main_menu, pattern=r"^nav:main_menu$"))
    application.add_handler(CallbackQueryHandler(handle_price_refresh, pattern=f"^{REFRESH_CALLBACK_DATA}$"))
    application.add_handler(build_alarm_conversation_handler())
    application.add_handler(build_profile_conversation_handler())

    # هندلر شکار پیام‌های متنی نامرتبط
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.Regex(_MENU_BUTTON_REGEX),
            handle_invalid_message
        )
    )

    # هندلر شکار دستورات نامعتبر و اشتباه
    application.add_handler(
        MessageHandler(
            filters.COMMAND,
            handle_unknown_command
        )
    )

    return application


async def process_update(update_data: dict, application: Application) -> None:
    update = Update.de_json(update_data, application.bot)
    await application.process_update(update)
