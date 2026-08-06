"""Goal-Oriented Collector — channel-driven collection with targets.

Replaces the legacy source-driven collector when gpcg_editorial_brief_enabled
is True. Instead of collecting everything from all feeds, it:

1. Consults the feeds defined in the Editorial Brief
2. Runs the expanded search queries (game + editorial keywords)
3. Tracks collection targets per item_type
4. Stops early when all targets are met (reduces processing + LLM cost)
5. Attributes KIs with the correct item_type from the search template

The collector reuses the existing RSS infrastructure (feedparser, content_hash
deduplication) from content_collectors.py — it only changes WHAT is collected
and HOW MUCH, not the mechanics of fetching and hashing.

See docs/EDITORIAL_INTELLIGENCE_V2_PROPOSAL.md §9.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.application.content_collectors import (
    _compute_hash,
    _is_duplicate,
)
from gpcg.domain.editorial_types import (
    CollectionResult,
    EditorialBrief,
    FeedSpec,
    SearchQuery,
)
from gpcg.domain.models import (
    Game,
    KnowledgeItem,
    KnowledgeItemSource,
    KnowledgeItemStatus,
)
from gpcg.logging import get_logger

log = get_logger(__name__)


class GoalOrientedCollector:
    """Collects KIs guided by an Editorial Brief with per-type targets."""

    def collect(
        self,
        session: Session,
        brief: EditorialBrief,
        user_id: int,
    ) -> CollectionResult:
        """Execute the collection plan.

        Returns a CollectionResult with collected KIs and any unmet targets.
        KIs are persisted to the session (caller commits).
        """
        collected: dict[str, list[KnowledgeItem]] = defaultdict(list)
        remaining = dict(brief.collection_targets)
        queries_executed = 0
        feeds_consulted = 0

        # Phase 1: Collect from feeds (RSS)
        for feed in brief.feeds:
            if self._all_targets_met(remaining):
                log.info(f"GoalOrientedCollector: all targets met, stopping feed collection")
                break

            items = self._collect_from_feed(session, feed, brief, user_id)
            feeds_consulted += 1
            for ki in items:
                if remaining.get(ki.item_type, 0) > 0:
                    collected[ki.item_type].append(ki)
                    remaining[ki.item_type] -= 1

        # Phase 2: Collect from search queries (Google News RSS)
        for query in brief.search_queries:
            if self._all_targets_met(remaining):
                log.info(f"GoalOrientedCollector: all targets met, stopping query collection")
                break

            items = self._collect_from_query(session, query, brief, user_id)
            queries_executed += 1
            for ki in items:
                if remaining.get(ki.item_type, 0) > 0:
                    collected[ki.item_type].append(ki)
                    remaining[ki.item_type] -= 1

        total = sum(len(v) for v in collected.values())
        result = CollectionResult(
            collected=dict(collected),
            remaining=remaining,
            total=total,
            queries_executed=queries_executed,
            feeds_consulted=feeds_consulted,
        )
        log.info(
            f"GoalOrientedCollector: collected {total} KIs "
            f"({feeds_consulted} feeds, {queries_executed} queries), "
            f"remaining={remaining}"
        )
        return result

    # ── Helpers ────────────────────────────────────────────────────────────

    def _all_targets_met(self, remaining: dict[str, int]) -> bool:
        """True if all collection targets are met (remaining ≤ 0)."""
        return all(v <= 0 for v in remaining.values())

    def _collect_from_feed(
        self,
        session: Session,
        feed: FeedSpec,
        brief: EditorialBrief,
        user_id: int,
    ) -> list[KnowledgeItem]:
        """Collect items from a single RSS feed."""
        try:
            import feedparser
        except ImportError:
            log.error("feedparser not installed — run: pip install feedparser")
            return []

        try:
            parsed = feedparser.parse(feed.url)
        except Exception as e:
            log.warning(f"Failed to fetch feed {feed.source_name}: {e}")
            return []

        if not parsed.entries:
            return []

        # Global feeds → user_id NULL (shared pool). Channel feeds → user_id set.
        ki_user_id = None if feed.scope == "global" else user_id
        is_public = feed.scope == "global"  # global = shared

        items: list[KnowledgeItem] = []
        for entry in parsed.entries[:20]:  # cap per feed
            ki = self._entry_to_ki(
                entry,
                source_name=feed.source_name,
                item_type=feed.item_type,
                user_id=ki_user_id,
                is_public=is_public,
                game_id=None,  # feeds are general, not game-specific
            )
            if ki and not _is_duplicate(session, ki):
                session.add(ki)
                session.flush()
                items.append(ki)
        return items

    def _collect_from_query(
        self,
        session: Session,
        query: SearchQuery,
        brief: EditorialBrief,
        user_id: int,
    ) -> list[KnowledgeItem]:
        """Collect items from a Google News RSS search query."""
        try:
            import feedparser
        except ImportError:
            return []

        from urllib.parse import quote
        from gpcg.config import get_settings
        settings = get_settings()
        feed_url = settings.gpcg_rss_feed_url.format(query=quote(query.text))

        try:
            parsed = feedparser.parse(feed_url)
        except Exception as e:
            log.warning(f"Failed to fetch query '{query.text}': {e}")
            return []

        if not parsed.entries:
            return []

        # Query-based KIs are per-channel (user_id set) because they were
        # searched specifically for this channel's brief.
        items: list[KnowledgeItem] = []
        for entry in parsed.entries[:10]:  # cap per query
            ki = self._entry_to_ki(
                entry,
                source_name="Google News",
                item_type=query.item_type,
                user_id=user_id,
                is_public=False,
                game_id=query.game_id,
            )
            if ki and not _is_duplicate(session, ki):
                session.add(ki)
                session.flush()
                items.append(ki)
        return items

    def _entry_to_ki(
        self,
        entry,
        *,
        source_name: str,
        item_type: str,
        user_id: Optional[int],
        is_public: bool,
        game_id: Optional[int],
    ) -> Optional[KnowledgeItem]:
        """Convert an RSS entry to a KnowledgeItem (without saving)."""
        title = getattr(entry, "title", "").strip()
        if not title:
            return None

        # Content: prefer summary, fallback to description
        content = ""
        if hasattr(entry, "summary") and entry.summary:
            content = entry.summary
        elif hasattr(entry, "description") and entry.description:
            content = entry.description
        content = content.strip()

        # Published date
        published_at = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            import time
            published_at = datetime_from_time(entry.published_parsed)
        elif hasattr(entry, "published") and entry.published:
            # feedparser provides a string; try to parse
            try:
                from email.utils import parsedate_to_datetime
                published_at = parsedate_to_datetime(entry.published)
            except Exception:
                pass

        # Source URL
        source_url = None
        if hasattr(entry, "link") and entry.link:
            source_url = entry.link

        content_hash = _compute_hash(title, content)

        return KnowledgeItem(
            user_id=user_id,
            is_public=is_public,
            title=title[:500],
            content=content,
            item_type=item_type,
            source_type=KnowledgeItemSource.rss.value,
            source_url=source_url,
            source_name=source_name,
            published_at=published_at,
            content_hash=content_hash,
            status=KnowledgeItemStatus.fresh.value,
            editorial_score=0.0,  # scored later by score_all_fresh
            game_id=game_id,
        )


def datetime_from_time(t_struct):
    """Convert a time.struct_time to datetime."""
    from datetime import datetime
    try:
        return datetime(*t_struct[:6])
    except Exception:
        return None
