"""
Keyboard builders shared across all handler modules.

Includes the persistent Reply Keyboard main menu, the standardized inline
footer patterns ("back + cancel" / "back + main menu"), and the option-row
builders for the alarm creation flow's condition/frequency steps.
"""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

# ---------------------------------------------------------------------------
# Persistent main menu (Reply Keyboard) — v1.2
# ---------------------------------------------------------------------------
BTN_ALARM = "🔔 تنظیم هشدار قیمت"
BTN_PRICE = "💵 قیمت الان"
BTN_NEWS = "📰 اخبار روز"
BTN_PROFILE = "👤 پروفایل کاربری"

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_ALARM, BTN_PRICE], [BTN_NEWS, BTN_PROFILE]],
    resize_keyboard=True,
)

_MENU_BUTTON_TEXTS = [BTN_ALARM, BTN_PRICE, BTN_NEWS, BTN_PROFILE]

NAV_MAIN_MENU_CALLBACK = "nav:main_menu"
NAV_CANCEL_CALLBACK = "nav:cancel_flow"

REFRESH_CALLBACK_DATA = "price_refresh"


def _with_cancel_footer(
    rows: list[list[InlineKeyboardButton]], back_callback: str | None = None
) -> InlineKeyboardMarkup:
    """
    تولید هدر دکمه‌ها به همراه دکمه بازگشت (در صورت وجود) و دکمه یکپارچه لغو فرآیند
    """
    footer = []
    if back_callback:
        footer.append(InlineKeyboardButton("🔙 مرحله قبل", callback_data=back_callback))
    footer.append(InlineKeyboardButton("❌ لغو فرآیند", callback_data=NAV_CANCEL_CALLBACK))
    return InlineKeyboardMarkup(rows + [footer])


def _with_footer(
    rows: list[list[InlineKeyboardButton]], back_callback: str
) -> InlineKeyboardMarkup:
    footer = [
        InlineKeyboardButton("🔙 مرحله قبل", callback_data=back_callback),
        InlineKeyboardButton("🏠 منوی اصلی", callback_data=NAV_MAIN_MENU_CALLBACK),
    ]
    return InlineKeyboardMarkup(rows + [footer])


def _get_price_keyboard() -> InlineKeyboardMarkup:
    """تولید دکمه شیشه‌ای فعال به‌روزرسانی"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 به‌روزرسانی", callback_data=REFRESH_CALLBACK_DATA)]
    ])


def _condition_option_rows() -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton("📈 بالاتر از این قیمت",     callback_data="condition:above")],
        [InlineKeyboardButton("📉 پایین‌تر از این قیمت",   callback_data="condition:below")],
        [InlineKeyboardButton("🚀 افزایش بیش از این درصد", callback_data="condition:percentage_up")],
        [InlineKeyboardButton("🔻 کاهش بیش از این درصد",  callback_data="condition:percentage_down")],
    ]


def _frequency_option_rows() -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton("1️⃣ فقط یک‌بار", callback_data="frequency:once")],
        [InlineKeyboardButton("📅 روزی یک‌بار", callback_data="frequency:daily")],
        [InlineKeyboardButton("🔁 هر بار",       callback_data="frequency:every_time")],
    ]


def _profile_main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔔 مدیریت هشدارها", callback_data="profile:alarms")],
        [InlineKeyboardButton("📰 مدیریت اخبار", callback_data="profile:news")],
    ]
    return _with_footer(rows, back_callback=NAV_MAIN_MENU_CALLBACK)
