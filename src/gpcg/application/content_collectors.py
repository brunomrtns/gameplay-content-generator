"""Content collectors — RSS collection for KnowledgeItems (V2).

Collects external content (news, curiosity) from RSS feeds and stores
as KnowledgeItems with editorial scoring.

See ARCHITECTURE_V2.md §7.5 (Coleta).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.models import Game, KnowledgeItem, KnowledgeItemSource, KnowledgeItemStatus, KnowledgeItemType
from gpcg.logging import get_logger

log = get_logger(__name__)


def collect_rss(
    session: Session,
    game_id: int,
    *,
    since: Optional[datetime] = None,
    max_items: int = 20,
) -> int:
    """Collect news via Google News RSS for a game.

    Returns the number of new KnowledgeItems created (deduped).
    """
    try:
        import feedparser
    except ImportError:
        log.error("feedparser not installed — run: pip install feedparser")
        return 0

    game = session.get(Game, game_id)
    if not game:
        log.error(f"collect_rss: Game #{game_id} not found")
        return 0

    settings = get_settings()
    query = quote(f"{game.canonical_name} game")
    feed_url = settings.gpcg_rss_feed_url.format(query=query)

    log.info(f"collect_rss: fetching RSS for '{game.canonical_name}' from {feed_url}")
    feed = feedparser.parse(feed_url)

    if not feed.entries:
        log.info(f"collect_rss: no entries found for '{game.canonical_name}'")
        return 0

    count = 0
    for entry in feed.entries[:max_items]:
        item = _normalize_rss_entry(entry, game)
        if item and not _is_duplicate(session, item):
            session.add(item)
            session.flush()
            count += 1

    log.info(f"collect_rss: collected {count} new items for '{game.canonical_name}'")
    return count


def _normalize_rss_entry(entry, game: Game) -> Optional[KnowledgeItem]:
    """Convert an RSS feed entry to a KnowledgeItem."""
    title = _clean_text(entry.get("title", ""))
    if not title:
        return None

    # Content: use summary or description
    content = _clean_text(entry.get("summary", "") or entry.get("description", ""))
    if not content:
        content = title  # fallback: use title as content

    # Truncate content to reasonable length
    if len(content) > 2000:
        content = content[:2000] + "..."

    # Published date
    published_at = _parse_date(entry.get("published_parsed") or entry.get("updated_parsed"))

    # Source URL
    link = entry.get("link", "")
    source_name = _extract_source_name(entry, link)

    # Compute content hash for dedup
    content_hash = _compute_hash(title, content)

    # Denormalize franchise/developer from game for filter-without-JOIN
    item = KnowledgeItem(
        game_id=game.id,
        title=title,
        content=content,
        item_type=KnowledgeItemType.news.value,
        source_type=KnowledgeItemSource.rss.value,
        source_url=link if link else None,
        source_name=source_name,
        published_at=published_at,
        collected_at=datetime.now(timezone.utc),
        editorial_score=0.0,  # will be scored by score_knowledge_item
        status=KnowledgeItemStatus.fresh.value,
        franchise=game.franchise,
        developer=game.developer,
        tags=[],
        content_hash=content_hash,
    )
    return item


def _is_duplicate(session: Session, item: KnowledgeItem) -> bool:
    """Check if a KnowledgeItem with the same content_hash already exists."""
    if not item.content_hash:
        return False
    existing = session.execute(
        select(KnowledgeItem.id).where(KnowledgeItem.content_hash == item.content_hash)
    ).scalar_one_or_none()
    return existing is not None


def _compute_hash(title: str, content: str) -> str:
    """Compute SHA256 of normalized title + content[:500]."""
    normalized = _normalize_for_hash(title) + _normalize_for_hash(content[:500])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_for_hash(text: str) -> str:
    """Normalize text for hashing: lowercase, strip, collapse whitespace."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _clean_text(text: str) -> str:
    """Strip HTML tags and clean up text from RSS feeds."""
    if not text:
        return ""
    # Remove HTML tags
    clean = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _parse_date(time_struct) -> Optional[datetime]:
    """Parse a time.struct_time from feedparser to datetime."""
    if not time_struct:
        return None
    try:
        from time import mktime
        dt = datetime.fromtimestamp(mktime(time_struct), tz=timezone.utc)
        return dt
    except (ValueError, OverflowError, OSError):
        return None


def _extract_source_name(entry, link: str) -> Optional[str]:
    """Extract a human-readable source name from an RSS entry."""
    # Try feed title first
    if hasattr(entry, "feed") and hasattr(entry.feed, "title"):
        return entry.feed.title
    # Try parsing from link domain
    if link:
        from urllib.parse import urlparse
        try:
            domain = urlparse(link).netloc
            if domain:
                return domain.replace("www.", "")
        except Exception:
            pass
    return None


def cleanup_old_news(session: Session, *, days: int = 30) -> int:
    """Delete news KnowledgeItems older than N days.

    Only deletes 'news' type items with status='fresh'. Evergreen items
    (curiosity, lore) are retained indefinitely.

    Returns the number of items deleted.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old_news = session.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.item_type == KnowledgeItemType.news.value,
            KnowledgeItem.status == KnowledgeItemStatus.fresh.value,
            KnowledgeItem.published_at < cutoff,
        )
    ).scalars().all()

    count = len(old_news)
    for item in old_news:
        session.delete(item)

    if count > 0:
        log.info(f"cleanup_old_news: deleted {count} news items older than {days} days")
    return count
