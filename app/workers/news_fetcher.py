"""
RSS news failover chain.

Only one source is actively polled per cycle: we try the first entry in
`settings.rss_source_priority`, and only fall through to the next one if
the current source fails outright (network error, unparseable feed).
Deduplication is a simple in-memory set of seen URLs — no DB table for
this in Week 1, matching the "simple URL-based deduplication" requirement.
"""
import logging
from typing import TypedDict

import feedparser
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)

# In-memory dedup set. Process-lifetime only — acceptable for Week 1 since
# a restart just means a handful of already-seen items get reconsidered
# once, not repeatedly.
_seen_urls: set[str] = set()


class NewsItem(TypedDict):
    title: str
    url: str
    source: str


def _matches_keywords(title: str, summary: str) -> bool:
    haystack = f"{title} {summary}".lower()
    return any(keyword in haystack for keyword in settings.news_keywords)


async def _fetch_feed(url: str) -> feedparser.FeedParserDict:
    # follow_redirects=True is required: httpx does NOT follow redirects by
    # default, and Coindesk (among others) 308-redirects its feed URL.
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return feedparser.parse(resp.content)


async def get_latest_news() -> list[NewsItem]:
    """
    Try each RSS source in priority order until one responds successfully.
    Return new (not-yet-seen), keyword-matching items from that source.
    """
    for rss_source in settings.rss_source_priority:
        name = rss_source["name"]
        url = rss_source["url"]

        try:
            feed = await _fetch_feed(url)
        except Exception:
            logger.exception("RSS source %s failed, trying next in chain", name)
            continue

        if feed.bozo and not feed.entries:
            logger.warning("RSS source %s returned unparseable feed, trying next", name)
            continue

        new_items: list[NewsItem] = []
        for entry in feed.entries:
            entry_url = entry.get("link")
            if not entry_url or entry_url in _seen_urls:
                continue

            title = entry.get("title", "")
            summary = entry.get("summary", "")

            if not _matches_keywords(title, summary):
                continue

            _seen_urls.add(entry_url)
            new_items.append(NewsItem(title=title, url=entry_url, source=name))

        # Successfully polled this source this cycle — stop here even if
        # it returned zero new items, since "single active source" means
        # we don't also poll the next one down the chain in the same pass.
        return new_items

    logger.error("All RSS sources failed for this poll cycle")
    return []

