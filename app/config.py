"""
Environment configuration and static priority/keyword tables.

Loaded once at import time. Keep this module free of any I/O beyond
reading environment variables — no network calls, no DB access — so it
stays safe to import from anywhere (including at process startup before
the DB or bot are ready).
"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- Telegram ---
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

    # --- Database ---
    database_path: str = os.getenv("DATABASE_PATH", "./tether_tracker.db")

    # --- Market data sources ---
    wallex_api_url: str = os.getenv("WALLEX_API_URL", "https://api.wallex.ir/v1/markets")
    bitpin_api_url: str = os.getenv("BITPIN_API_URL", "https://api.bitpin.ir/v1/mkt/markets/")
    tetherland_api_url: str = os.getenv("TETHERLAND_API_URL", "https://api.tetherland.com/currencies")

    # --- Polling intervals (seconds) ---
    market_poll_interval: int = _env_int("MARKET_POLL_INTERVAL", 3)
    rss_poll_interval: int = _env_int("RSS_POLL_INTERVAL", 300)

    # --- Misc ---
    env: str = os.getenv("ENV", "development")

    # --- Market failover priority chain (Bitpin set as Primary) ---
    market_source_priority: tuple = field(
        default_factory=lambda: ("bitpin", "wallex", "tetherland")
    )

    # --- RSS source priority chain (اقتصادی، رمزپایه و سیاسی) ---
    rss_source_priority: tuple = field(
        default_factory=lambda: (
            # --- اخبار اقتصادی و کریپتو ---
            {
                "slug": "irna_macro",
                "name": "ایرنا (اقتصاد کلان)",
                "url": "https://www.irna.ir/rss/tp/27",
            },
            {
                "slug": "donya_e_eqtesad",
                "name": "دنیای اقتصاد",
                "url": "https://donya-e-eqtesad.com/feeds",
            },
            {
                "slug": "eghtesadnews",
                "name": "اقتصادنیوز",
                "url": "https://www.eghtesadnews.com/rss",
            },
            {
                "slug": "ecoiran",
                "name": "اکوایران",
                "url": "https://ecoiran.com/feeds",
            },
            {
                "slug": "eghtesadonline",
                "name": "اقتصاد آنلاین",
                "url": "https://www.eghtesadonline.com/rss",
            },
            {
                "slug": "tejaratnews",
                "name": "تجارت‌نیوز",
                "url": "https://tejaratnews.com/feed",
            },
            {
                "slug": "ramzarz_news",
                "name": "رمز ارز نیوز",
                "url": "https://ramzarz.news/feed/",
            },
            {
                "slug": "arzdigital",
                "name": "ارز دیجیتال",
                "url": "https://arzdigital.com/breaking/feed/",
            },
            # --- اخبار سیاسی و بین‌الملل ---
            {
                "slug": "irna_defense",
                "name": "ایرنا (دفاعی امنیتی)",
                "url": "https://www.irna.ir/rss/tp/9",
            },
            {
                "slug": "irna_mfa",
                "name": "ایرنا (دیپلماسی و خارجه)",
                "url": "https://www.irna.ir/rss/tp/1003422",
            },
            {
                "slug": "bbc_persian",
                "name": "بی‌بی‌سی فارسی",
                "url": "https://feeds.bbci.co.uk/persian/rss.xml",
            },
            {
                "slug": "iran_intl",
                "name": "ایران اینترنشنال",
                "url": "https://www.iranintl.com/feed",
            },
            {
                "slug": "euronews_fa",
                "name": "یورونیوز فارسی",
                "url": "https://parsi.euronews.com/rss",
            },
        )
    )

    # --- Keyword filter matrix for news relevance ---
    news_keywords: tuple = field(
        default_factory=lambda: (
            "تتر",
            "دلار",
            "ارز",
            "قیمت دلار",
            "بازار ارز",
            "بانک مرکزی",
            "نیما",
            "صرافی",
            "کریپتو",
            "بیت کوین",
            "تحریم",
            "برجام",
            "مذاکرات",
            "tether",
            "usdt",
        )
    )


settings = Settings()