"""
Smart News & Market Signals Telegram Handlers (v1.0).

Handles:
- Live in-place news rendering (editMessageText)
- Pagination (5 news per page)
- Empty state with Quick-Edit and "Show All" fallbacks
- Inline source toggling directly inside the news flow
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
from app.config import settings
from app.bot.handlers.keyboards import BTN_NEWS, _MENU_BUTTON_TEXTS, NAV_MAIN_MENU_CALLBACK
from app.bot.handlers.nav import handle_global_interrupt, handle_nav_main_menu

_MENU_BUTTON_REGEX = "^(" + "|".join(re.escape(t) for t in _MENU_BUTTON_TEXTS) + ")$"

NEWS_STATE_MAIN = 1
NEWS_STATE_QUICK_EDIT = 2


def _build_news_keyboard(
        page: int,
        total_count: int,
        page_size: int = 5
) -> InlineKeyboardMarkup:
    """ساخت دکمه‌های شیشه‌ای صفحه‌بندی اخبار"""
    total_pages = (total_count + page_size - 1) // page_size
    nav_row = []

    if page > 1:
        nav_row.append(InlineKeyboardButton("➡️ صفحه قبل", callback_data=f"news_page:{page - 1}"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("⬅️ صفحه بعد", callback_data=f"news_page:{page + 1}"))

    rows = []
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data=NAV_MAIN_MENU_CALLBACK)])
    return InlineKeyboardMarkup(rows)


async def _render_news_page(
        chat_id: int,
        page: int = 1,
        ignore_user_sources: bool = False
) -> tuple[str, InlineKeyboardMarkup, bool]:
    """
    رندر کردن متن و دکمه‌های صفحه اخبار.
    برمی‌گرداند: (متن, کیبورد, آیا خبری یافت شد؟)
    """
    items, total_count = await database.get_filtered_news(
        chat_id=chat_id,
        page=page,
        page_size=5,
        ignore_user_sources=ignore_user_sources
    )

    if total_count == 0:
        user_sources = await database.get_user_news_sources(chat_id)
        if not user_sources and not ignore_user_sources:
            text = (
                "📭 *شما هیچ منبع خبری را انتخاب نکرده‌اید.*\n\n"
                "لطفاً از بخش ویرایش سریع منابع، حداقل یک منبع را فعال کنید یا روی نمایش اخبار همه منابع بزنید:"
            )
        else:
            slug_to_name = {
                src.get("slug", src["name"].lower().replace(" ", "_")): src["name"]
                for src in settings.rss_source_priority
            }
            source_names = [slug_to_name.get(s, s) for s in user_sources]
            source_names_str = ", ".join(source_names) if source_names else "همه منابع"

            text = (
                f"📭 *در ۲۴ ساعت گذشته خبری با کیفیت تحلیلی بالا از منابع انتخابی شما ({source_names_str}) ثبت نشده است.*\n\n"
                "می‌توانی از دکمه‌های زیر برای دیدن اخبار تمام منابع یا تغییر سریع منابع خود استفاده کنی:"
            )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ ویرایش سریع منابع", callback_data="news_action:quick_edit")],
            [InlineKeyboardButton("🌐 نمایش اخبار همه منابع", callback_data="news_action:show_all")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data=NAV_MAIN_MENU_CALLBACK)],
        ])
        return text, keyboard, False

    total_pages = (total_count + 4) // 5
    lines = [f"📰 *اخبار و سیگنال‌های هوشمند بازار* (صفحه {page} از {total_pages})\n", "――――――――――――\n"]

    for i, item in enumerate(items, start=1):
        title = item["title"].strip()
        summary = item["summary"].strip() if item.get("summary") else ""
        source = item["source_name"].strip()
        link = item["link"].strip()

        # ۱. عنوان بدون لینک و به‌صورت BOLD
        lines.append(f"*{i}. {title}*")
        lines.append(f"🏛 منبع: `{source}`")

        # ۲. اضافه کردن خلاصه متن همراه با کلمه «مشاهده منبع» هایپرلینک شده
        if summary:
            lines.append(f"📝 {summary[:150]}... [مشاهده منبع]({link})")
        else:
            lines.append(f"📝 [مشاهده منبع]({link})")
        lines.append("")

    text = "\n".join(lines)
    keyboard = _build_news_keyboard(page=page, total_count=total_count)
    return text, keyboard, True


async def _render_quick_edit_menu(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """رندر لیست انتخاب سریع منابع از داخل فلو اخبار"""
    subscribed = set(await database.get_user_news_sources(chat_id))

    rows = []
    for rss_source in settings.rss_source_priority:
        name = rss_source["name"]
        slug = rss_source.get("slug", name.lower().replace(" ", "_"))
        icon = "✅" if slug in subscribed else "⬛️"
        rows.append([InlineKeyboardButton(f"{icon} {name}", callback_data=f"news_quick_toggle:{slug}")])

    rows.append([
        InlineKeyboardButton("🔄 دریافت مجدد اخبار", callback_data="news_action:reload"),
        InlineKeyboardButton("🏠 منوی اصلی", callback_data=NAV_MAIN_MENU_CALLBACK),
    ])

    text = "⚙️ *ویرایش سریع منابع خبری*\n――――――――――――\n\nروی هر منبع بزن تا تغییر کند، سپس روی دریافت مجدد کلیک کن:"
    return text, InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_news_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ورود به فلو اخبار با ویرایش درجای پیام یا پاسخ به دکمه منوی اصلی"""
    chat_id = update.effective_chat.id
    text, keyboard, _ = await _render_news_page(chat_id=chat_id, page=1)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

    return NEWS_STATE_MAIN


async def handle_news_paginate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    page = int(query.data.split(":", 1)[1])
    ignore_all = context.user_data.get("news_ignore_sources", False)

    text, keyboard, _ = await _render_news_page(chat_id=chat_id, page=page, ignore_user_sources=ignore_all)
    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    return NEWS_STATE_MAIN


async def handle_news_show_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    context.user_data["news_ignore_sources"] = True

    text, keyboard, _ = await _render_news_page(chat_id=chat_id, page=1, ignore_user_sources=True)
    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    return NEWS_STATE_MAIN


async def handle_news_quick_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    text, keyboard = await _render_quick_edit_menu(chat_id)
    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    return NEWS_STATE_QUICK_EDIT


async def handle_news_quick_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    slug = query.data.split(":", 1)[1]

    await database.toggle_news_source(chat_id, slug)

    text, keyboard = await _render_quick_edit_menu(chat_id)
    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    return NEWS_STATE_QUICK_EDIT


async def handle_news_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    context.user_data["news_ignore_sources"] = False

    text, keyboard, _ = await _render_news_page(chat_id=chat_id, page=1, ignore_user_sources=False)
    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    return NEWS_STATE_MAIN


def build_news_conversation_handler() -> ConversationHandler:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=PTBUserWarning)
        return ConversationHandler(
            entry_points=[
                CommandHandler("news", handle_news_entry),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_NEWS)}$"), handle_news_entry),
            ],
            states={
                NEWS_STATE_MAIN: [
                    CallbackQueryHandler(handle_news_paginate, pattern=r"^news_page:\d+$"),
                    CallbackQueryHandler(handle_news_show_all, pattern=r"^news_action:show_all$"),
                    CallbackQueryHandler(handle_news_quick_edit_start, pattern=r"^news_action:quick_edit$"),
                ],
                NEWS_STATE_QUICK_EDIT: [
                    CallbackQueryHandler(handle_news_quick_toggle, pattern=r"^news_quick_toggle:"),
                    CallbackQueryHandler(handle_news_reload, pattern=r"^news_action:reload$"),
                ],
            },
            fallbacks=[
                CallbackQueryHandler(handle_nav_main_menu, pattern=r"^nav:main_menu$"),
                MessageHandler(filters.COMMAND | filters.Regex(_MENU_BUTTON_REGEX), handle_global_interrupt),
            ],
            per_message=False,
        )