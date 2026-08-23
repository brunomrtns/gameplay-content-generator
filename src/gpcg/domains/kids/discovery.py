"""Kids Idea Discovery — generates KidsIdeas from multiple sources.

Sources:
1. AI Ideation — LLM generates ideas from channel profile + categories
2. Topic Library — seed topics from the built-in library
3. Seasonal Calendar — seasonal themes (holidays, events)

The discovery service creates KidsIdeas with status=discovered. Safety
review and scoring are done separately (via the score endpoint or the
kids_idea_score job).

Deduplication is handled by create_idea() — if an idea with the same
content_hash or high similarity already exists, it's skipped.

Factuality: AI-generated ideas are editorial prompts, NOT verified facts.
The script pipeline handles fact validation (the script prompt instructs
the LLM to not invent facts, and the originality layer checks the output).
This service does NOT validate facts — it only generates ideas.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from gpcg.core.models import ChannelProfile
from gpcg.domains.kids.idea_service import create_idea
from gpcg.domains.kids.models import KidsIdea, KidsIdeaSource
from gpcg.domains.kids.topic_library import (
    TopicCategory,
    TopicSeed,
    get_all_categories,
    get_category,
    get_seeds_for_category,
)
from gpcg.domains.kids.seasonal_calendar import (
    SeasonalEntry,
    get_active_seasonal,
)
from gpcg.infrastructure.llm import LLMClient, LLMError, get_llm
from gpcg.logging import get_logger

log = get_logger(__name__)


class KidsIdeaDiscovery:
    """Discovers new KidsIdeas from multiple sources.

    Usage::

        discovery = KidsIdeaDiscovery(llm=get_llm())
        result = discovery.discover(
            session=db_session,
            user_id=user_id,
            profile=channel_profile,
            categories=["animals", "science"],
            ideas_per_category=3,
            include_seasonal=True,
        )
        # result.created_count, result.skipped_count, result.errors
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm

    def discover(
        self,
        session: Session,
        user_id: int,
        profile: ChannelProfile,
        *,
        categories: Optional[list[str]] = None,
        ideas_per_category: int = 3,
        include_seasonal: bool = True,
        include_topic_library: bool = True,
    ) -> "DiscoveryResult":
        """Run discovery and create new KidsIdeas.

        Args:
            session: DB session.
            user_id: User ID to create ideas for.
            profile: Channel profile (for age range, niche, context).
            categories: Categories to focus on (defaults to all in library).
            ideas_per_category: How many ideas to generate per category via AI.
            include_seasonal: Whether to include seasonal themes.
            include_topic_library: Whether to include topic library seeds.

        Returns:
            DiscoveryResult with counts and details.
        """
        result = DiscoveryResult()

        # Determine categories
        if categories:
            cats = [get_category(c) for c in categories if get_category(c)]
            cats = [c for c in cats if c is not None]
        else:
            cats = get_all_categories()

        # Get channel context
        age_range = self._get_age_range(profile)
        channel_context = profile.to_prompt_context()

        # 1. AI Ideation
        for cat in cats:
            try:
                ai_ideas = self._ai_ideation(
                    cat, age_range, channel_context, ideas_per_category
                )
                for idea_data in ai_ideas:
                    idea = create_idea(
                        session, user_id,
                        title=idea_data["title"],
                        description=idea_data.get("description", ""),
                        category=idea_data.get("category", cat.name),
                        suggested_age_range=idea_data.get("suggested_age_range", age_range),
                        source=KidsIdeaSource.ai_ideation.value,
                        source_metadata={
                            "category": cat.name,
                            "channel_context": channel_context[:200],
                        },
                    )
                    if idea:
                        result.created_count += 1
                        result.created_titles.append(idea.title)
                    else:
                        result.skipped_count += 1
            except Exception as e:
                log.warning(f"discovery.ai_ideation_failed: category={cat.name}, error={e}")
                result.errors.append(f"ai_ideation:{cat.name}:{e}")

        # 2. Topic Library seeds
        if include_topic_library:
            for cat in cats:
                for seed in get_seeds_for_category(cat.name):
                    idea = create_idea(
                        session, user_id,
                        title=seed.title_hint,
                        description=seed.description,
                        category=cat.name,
                        suggested_age_range=age_range,
                        source=KidsIdeaSource.topic_library.value,
                        source_metadata={"category": cat.name, "seed": True},
                    )
                    if idea:
                        result.created_count += 1
                        result.created_titles.append(idea.title)
                    else:
                        result.skipped_count += 1

        # 3. Seasonal
        if include_seasonal:
            seasonal_entries = get_active_seasonal()
            for entry in seasonal_entries:
                try:
                    seasonal_ideas = self._seasonal_ideation(
                        entry, age_range, channel_context
                    )
                    for idea_data in seasonal_ideas:
                        idea = create_idea(
                            session, user_id,
                            title=idea_data["title"],
                            description=idea_data.get("description", ""),
                            category=idea_data.get("category", entry.category),
                            suggested_age_range=idea_data.get("suggested_age_range", age_range),
                            source=KidsIdeaSource.seasonal.value,
                            source_metadata={
                                "seasonal_entry": entry.name,
                                "date": entry.date,
                            },
                        )
                        if idea:
                            result.created_count += 1
                            result.created_titles.append(idea.title)
                        else:
                            result.skipped_count += 1
                except Exception as e:
                    log.warning(f"discovery.seasonal_failed: entry={entry.name}, error={e}")
                    result.errors.append(f"seasonal:{entry.name}:{e}")

        session.flush()
        log.info(
            f"discovery.complete: created={result.created_count}, "
            f"skipped={result.skipped_count}, errors={len(result.errors)}"
        )
        return result

    # ── AI Ideation ────────────────────────────────────────────────────────

    def _ai_ideation(
        self,
        category: TopicCategory,
        age_range: str,
        channel_context: str,
        count: int,
    ) -> list[dict]:
        """Generate ideas via LLM for a specific category."""
        from gpcg.domains.kids.prompts import IDEATION_SYSTEM

        llm = self.llm or get_llm()

        # Build seed hints from the library
        seed_hints = "\n".join(
            f"- {s.title_hint}" for s in category.seeds[:3]
        )

        user_prompt = f"""Target age range: {age_range}
Category: {category.name} ({category.display_name})
Category description: {category.description}
Channel context: {channel_context or "(no specific context)"}
Number of ideas to generate: {count}

Seed examples for inspiration (do NOT repeat these exactly):
{seed_hints}

Generate {count} creative, educational ideas for this category."""

        try:
            data = llm.chat_json(
                system=IDEATION_SYSTEM,
                prompt=user_prompt,
                temperature=0.8,  # higher temperature for creativity
                max_tokens=1500,
            )
        except (LLMError, Exception) as e:
            log.warning(f"ideation.llm_failed: category={category.name}, error={e}")
            return []

        ideas = data.get("ideas", [])
        if not isinstance(ideas, list):
            return []

        # Validate and clean
        valid: list[dict] = []
        for item in ideas:
            if not isinstance(item, dict):
                continue
            title = item.get("title", "").strip()
            if not title or len(title) > 500:
                continue
            valid.append({
                "title": title,
                "description": str(item.get("description", "")).strip(),
                "category": str(item.get("category", category.name)).strip(),
                "suggested_age_range": str(item.get("suggested_age_range", age_range)).strip(),
            })

        return valid

    # ── Seasonal Ideation ──────────────────────────────────────────────────

    def _seasonal_ideation(
        self,
        entry: SeasonalEntry,
        age_range: str,
        channel_context: str,
    ) -> list[dict]:
        """Generate ideas for a seasonal entry."""
        from gpcg.domains.kids.prompts import IDEATION_SYSTEM

        llm = self.llm or get_llm()

        user_prompt = f"""Target age range: {age_range}
Category: {entry.category}
Channel context: {channel_context or "(no specific context)"}
Number of ideas to generate: 2

Seasonal theme: {entry.name} ({entry.date})
Description: {entry.description}

Generate 2 creative, educational ideas related to this seasonal theme.
The ideas should be kid-friendly and educational, not religious or political."""

        try:
            data = llm.chat_json(
                system=IDEATION_SYSTEM,
                prompt=user_prompt,
                temperature=0.7,
                max_tokens=800,
            )
        except (LLMError, Exception) as e:
            log.warning(f"seasonal.llm_failed: entry={entry.name}, error={e}")
            return []

        ideas = data.get("ideas", [])
        if not isinstance(ideas, list):
            return []

        valid: list[dict] = []
        for item in ideas:
            if not isinstance(item, dict):
                continue
            title = item.get("title", "").strip()
            if not title or len(title) > 500:
                continue
            valid.append({
                "title": title,
                "description": str(item.get("description", "")).strip(),
                "category": str(item.get("category", entry.category)).strip(),
                "suggested_age_range": str(item.get("suggested_age_range", age_range)).strip(),
            })

        return valid

    # ── Helpers ────────────────────────────────────────────────────────────

    def _get_age_range(self, profile: ChannelProfile) -> str:
        """Get target age range from channel profile."""
        meta = profile.metadata_json or {}
        return str(meta.get("kids_age_range", "3-6"))


class DiscoveryResult:
    """Result of a discovery run."""
    def __init__(self) -> None:
        self.created_count: int = 0
        self.skipped_count: int = 0
        self.errors: list[str] = []
        self.created_titles: list[str] = []

    def __repr__(self) -> str:
        return (
            f"<DiscoveryResult created={self.created_count} "
            f"skipped={self.skipped_count} errors={len(self.errors)}>"
        )
