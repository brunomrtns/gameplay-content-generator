"""Content collectors — RSS collection for KnowledgeItems (V2).

Collects external content (news, curiosity) from RSS feeds and stores
as KnowledgeItems with editorial scoring.

V2: `collect_rss_items()` is a headless version (no DB session) that
returns a list of dicts. Used by the remote worker to collect locally
and sync to VPS via API.

RSS sources:
- Google News RSS (per-game, via gpcg_rss_feed_url config)
- RSSHub public instance (gaming news aggregators: IGN, Kotaku, Polygon, etc.)
- Reddit r/games, r/gaming (via RSSHub)

See ARCHITECTURE_V2.md §7.5 (Coleta).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.models import Game, KnowledgeItem, KnowledgeItemSource, KnowledgeItemStatus, KnowledgeItemType
from gpcg.logging import get_logger

log = get_logger(__name__)

# RSSHub public instance — provides RSS feeds for sites that don't have native RSS
# Gaming routes: https://docs.rsshub.app/routes/game
RSSHUB_BASE = "https://rsshub.app"

# General gaming news feeds (not game-specific) — collected on every run
GENERAL_GAMING_FEEDS = [
    # Native RSS feeds
    {"url": "https://feeds.feedburner.com/ign/games-all", "source_name": "IGN", "item_type": "news"},
    {"url": "https://www.gamespot.com/feeds/mashup/", "source_name": "GameSpot", "item_type": "news"},
    {"url": "https://www.polygon.com/rss/index.xml", "source_name": "Polygon", "item_type": "news"},
    {"url": "https://www.eurogamer.net/feed", "source_name": "Eurogamer", "item_type": "news"},
    {"url": "https://www.rockpapershotgun.com/feed", "source_name": "Rock Paper Shotgun", "item_type": "news"},
    # RSSHub routes (gaming news aggregators)
    {"url": f"{RSSHUB_BASE}/kotaku/story/news", "source_name": "Kotaku", "item_type": "news"},
    {"url": f"{RSSHUB_BASE}/reddit/subreddit/games", "source_name": "r/games", "item_type": "news"},
    {"url": f"{RSSHUB_BASE}/reddit/subreddit/gaming", "source_name": "r/gaming", "item_type": "news"},
    # Curiosity/feature feeds
    {"url": f"{RSSHUB_BASE}/reddit/subreddit/truegaming", "source_name": "r/truegaming", "item_type": "curiosity"},
    {"url": f"{RSSHUB_BASE}/reddit/subreddit/patientgamers", "source_name": "r/patientgamers", "item_type": "curiosity"},
]


@dataclass
class RSSItem:
    """A collected RSS item (headless — no DB dependency)."""
    title: str
    content: str
    item_type: str = "news"
    source_type: str = "rss"
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    published_at: Optional[datetime] = None
    editorial_score: float = 0.0
    franchise: Optional[str] = None
    developer: Optional[str] = None
    game_id: Optional[int] = None
    content_hash: str = ""
    tags: list = field(default_factory=list)


def collect_rss_items(
    game_names: list[str] | None = None,
    *,
    max_per_game: int = 15,
    max_general: int = 30,
    search_queries: list[dict] | None = None,
) -> list[RSSItem]:
    """Collect RSS items headlessly (no DB session).

    Collects from:
    1. Google News RSS for each game name (game-specific news)
    2. General gaming news feeds (IGN, Kotaku, Polygon, Reddit, etc.)
    3. If search_queries provided: expanded editorial queries (curiosity/lore/etc.)

    Args:
        game_names: List of game canonical names to collect for.
                    If None, only collects general gaming news.
        max_per_game: Max items per game feed
        max_general: Max items per general feed
        search_queries: Optional list of {text, game_id, template_name, item_type}
                        from the Editorial Brief. Each query is searched on
                        Google News. Replaces the basic "{game} game" query
                        when provided, producing curiosity/lore items, not
                        just news.

    Returns:
        List of RSSItem dicts (deduped by content_hash).
    """
    try:
        import feedparser
    except ImportError:
        log.error("feedparser not installed — run: pip install feedparser")
        return []

    settings = get_settings()
    all_items: list[RSSItem] = []
    seen_hashes: set[str] = set()

    # 1. Game-specific feeds (Google News RSS)
    # If search_queries provided, use those instead of basic "{game} game"
    if search_queries:
        for sq in search_queries:
            query_text = sq.get("text", "")
            item_type = sq.get("item_type", "news")
            template_name = sq.get("template_name", "")
            game_id = sq.get("game_id")
            if not query_text:
                continue
            query = quote(query_text)
            feed_url = settings.gpcg_rss_feed_url.format(query=query)
            log.info(f"collect_rss_items: editorial query '{query_text}' (type={item_type}, template={template_name})")

            try:
                feed = feedparser.parse(feed_url)
            except Exception as e:
                log.warning(f"collect_rss_items: failed to fetch '{query_text}': {e}")
                continue

            for entry in feed.entries[:max_per_game]:
                item = _entry_to_rss_item(entry, item_type=item_type)
                if item and item.content_hash not in seen_hashes:
                    # Attach game_id from the search query so the KI is
                    # associated with the right game on the VPS
                    if game_id:
                        item.game_id = game_id
                    seen_hashes.add(item.content_hash)
                    all_items.append(item)
    elif game_names:
        # Legacy: basic "{game} game" query (news only)
        for game_name in game_names:
            query = quote(f"{game_name} game")
            feed_url = settings.gpcg_rss_feed_url.format(query=query)
            log.info(f"collect_rss_items: fetching Google News RSS for '{game_name}'")

            try:
                feed = feedparser.parse(feed_url)
            except Exception as e:
                log.warning(f"collect_rss_items: failed to fetch Google News for '{game_name}': {e}")
                continue

            for entry in feed.entries[:max_per_game]:
                item = _entry_to_rss_item(entry, game_name=game_name, item_type="news")
                if item and item.content_hash not in seen_hashes:
                    seen_hashes.add(item.content_hash)
                    all_items.append(item)

    # 2. General gaming news feeds
    for feed_info in GENERAL_GAMING_FEEDS:
        url = feed_info["url"]
        source_name = feed_info.get("source_name")
        item_type = feed_info.get("item_type", "news")
        log.info(f"collect_rss_items: fetching {source_name} from {url}")

        try:
            feed = feedparser.parse(url)
        except Exception as e:
            log.warning(f"collect_rss_items: failed to fetch {source_name}: {e}")
            continue

        for entry in feed.entries[:max_general]:
            item = _entry_to_rss_item(
                entry,
                source_name_override=source_name,
                item_type=item_type,
            )
            if item and item.content_hash not in seen_hashes:
                seen_hashes.add(item.content_hash)
                all_items.append(item)

    log.info(f"collect_rss_items: collected {len(all_items)} unique items")
    return all_items


def _entry_to_rss_item(
    entry,
    *,
    game_name: Optional[str] = None,
    source_name_override: Optional[str] = None,
    item_type: str = "news",
) -> Optional[RSSItem]:
    """Convert an RSS feed entry to an RSSItem (headless)."""
    title = _clean_text(entry.get("title", ""))
    if not title:
        return None

    content = _clean_text(entry.get("summary", "") or entry.get("description", ""))
    if not content:
        content = title
    if len(content) > 2000:
        content = content[:2000] + "..."

    published_at = _parse_date(entry.get("published_parsed") or entry.get("updated_parsed"))
    link = entry.get("link", "")
    source_name = source_name_override or _extract_source_name(entry, link)
    content_hash = _compute_hash(title, content)

    return RSSItem(
        title=title,
        content=content,
        item_type=item_type,
        source_type="rss",
        source_url=link if link else None,
        source_name=source_name,
        published_at=published_at,
        franchise=None,  # will be set by caller if game-specific
        game_id=None,
        content_hash=content_hash,
        tags=[game_name] if game_name else [],
    )


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
