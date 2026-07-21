"""
Smart Market News & Signals Service (v1.0).

Handles RSS fetching, Scoring Engine execution, text cleanup regex,
and DB persistence with 24-hour TTL expiration.
"""
import logging
import re
import time
from typing import TypedDict
from datetime import datetime

import feedparser
import httpx

from app import database
from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(12.0)

# ---------------------------------------------------------------------------
# Scoring Matrix & Regex Configurations
# ---------------------------------------------------------------------------

_HIGH_IMPACT_KEYWORDS = {
    "تنگه هرمز": 15,
    "پالایشگاه": 12,
    "آتش‌بس": 10,
    "تحریم": 10,
    "مذاکرات": 10,
    "حمله": 12,
    "جنگ": 12,
    "پهپاد": 10,
    "موشک": 10,
    "sanction": 10,
    "ceasefire": 10,
    "strait of hormuz": 15,
}

_MEDIUM_IMPACT_KEYWORDS = {
    "بانک مرکزی": 5,
    "تورم": 5,
    "ارز نیما": 5,
    "تتر": 5,
    "دلار": 4,
    "یورو": 4,
    "نرخ بهره": 5,
    "fed": 5,
    "inflation": 5,
    "sec": 5,
    "binance": 4,
    "tether": 5,
    "usdt": 5,
}

_BLACKLIST_KEYWORDS = {
    "قیمت امروز": -15,
    "کوییک": -20,
    "فوتبال": -20,
    "هواشناسی": -20,
    "وام": -10,
    "فیلم و سریال": -20,
    "قرعه کشی": -20,
    "پیش فروش": -15,
    "جدول قیمت": -15,
    "نرخ لحظه ای": -15,
}

# Regex پاکسازی پیشوندهای خبری (مانند «تهران-ایرنا-» یا «به گزارش ...»)
_SOURCE_PREFIX_REGEX = re.compile(
    r"^(?:[آ-یa-zA-Z\s]{2,15}\s*-\s*)?(?:به گزارش\s+[آ-یa-zA-Z\s]{2,20}،?\s*)?",
    re.UNICODE
)
_HTML_TAG_REGEX = re.compile(r"<[^>]+>")


def _clean_text(text: str) -> str:
    """پاکسازی تگ‌های HTML و پیشوندهای اضافه خبری از ابتدا متن"""
    if not text:
        return ""
    # حذف تگ‌های HTML
    cleaned = _HTML_TAG_REGEX.sub("", text)
    # حذف پیشوندهای رایج آژانس‌های خبری
    cleaned = _SOURCE_PREFIX_REGEX.sub("", cleaned).strip()
    return cleaned


def calculate_news_score(title: str, summary: str) -> int:
    """
    محاسبه امتیاز خبر بر اساس ماتریس کلمات کلیدی (+15 تا -20).
    قانون قبولی: score >= 10
    """
    text = f"{title} {summary}".lower()
    score = 0

    # بررسی کلمات بلک‌لیست
    for kw, weight in _BLACKLIST_KEYWORDS.items():
        if kw in text:
            score += weight

    # بررسی محرک‌های شدید
    for kw, weight in _HIGH_IMPACT_KEYWORDS.items():
        if kw in text:
            score += weight

    # بررسی محرک‌های مکمل
    for kw, weight in _MEDIUM_IMPACT_KEYWORDS.items():
        if kw in text:
            score += weight

    return score


def _parse_published_time(entry: feedparser.FeedParserDict) -> int:
    """استخراج دقیق timestamp زمان انتشار خبر"""
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return int(time.mktime(entry.published_parsed))
    elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return int(time.mktime(entry.updated_parsed))
    return int(time.time())


async def _fetch_feed(url: str) -> feedparser.FeedParserDict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True, headers=headers) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return feedparser.parse(resp.content)


async def fetch_and_process_news() -> int:
    """
    چرخه اصلی دریافت، ارزیابی و ذخیره اخبار در دیتابیس.
    خروجی: تعداد اخبار جدید ذخیره‌شده.
    """
    saved_count = 0

    for rss_source in settings.rss_source_priority:
        source_name = rss_source["name"]
        source_slug = rss_source.get("slug", source_name.lower().replace(" ", "_"))
        url = rss_source["url"]

        try:
            feed = await _fetch_feed(url)
        except Exception as e:
            logger.warning("Failed to fetch RSS from source %s (%s): %s", source_name, url, e)
            continue

        if feed.bozo and not feed.entries:
            logger.warning("Unparseable feed from source %s", source_name)
            continue

        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue

            raw_title = entry.get("title", "")
            raw_summary = entry.get("summary", "") or entry.get("description", "")

            cleaned_title = _clean_text(raw_title)
            cleaned_summary = _clean_text(raw_summary)

            score = calculate_news_score(cleaned_title, cleaned_summary)

            # فیلتر قبولی خبر (Score >= 10)
            if score < 10:
                continue

            published_at = _parse_published_time(entry)

            # ذخیره در دیتابیس (لینک تکراری INSERT نمی‌شود)
            inserted = await database.insert_news_item(
                title=cleaned_title,
                summary=cleaned_summary[:300],  # محدودیت طول خلاصه
                link=link,
                source_name=source_name,
                source_slug=source_slug,
                category="economic",
                score=score,
                published_at=published_at,
            )

            if inserted:
                saved_count += 1

    # پاکسازی اخبار قدیمی‌تر از ۲۴ ساعت
    await database.purge_expired_news(ttl_hours=24)

    return saved_count