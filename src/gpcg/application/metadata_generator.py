"""Metadata generator — LLM-powered social metadata for YouTube uploads.

Generates optimized title, description, and tags for video uploads using
the content plan (topic, hook, tone) and final script. Falls back to
simple truncation if the LLM call fails.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from gpcg.domain.models import ContentPlan, Game, Script
from gpcg.infrastructure.llm import LLMClient, LLMError

log = logging.getLogger(__name__)


@dataclass
class VideoMetadata:
    """Social metadata for a video upload."""

    title: str
    description: str
    tags: list[str]


class MetadataGenerator:
    """Generates social metadata (title, description, tags) via LLM."""

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm or LLMClient()

    def generate(
        self,
        plan: ContentPlan,
        script: Script,
        game: Optional[Game] = None,
        *,
        model: str = "",
    ) -> VideoMetadata:
        """Generate optimized metadata for a video.

        Args:
            plan: Content plan with topic, hook, tone.
            script: Final script text.
            game: Optional game (for game name in tags/title).
            model: Override LLM model (default: use LLMClient default).

        Returns:
            VideoMetadata with title (<=100 chars), description (<=5000 chars),
            and 5-15 tags.
        """
        game_name = game.canonical_name if game else ""
        script_text = (script.final or "")[:2000]  # Truncate for prompt size

        system = (
            "You are a YouTube SEO specialist for gaming content. "
            "Generate catchy, click-worthy metadata optimized for YouTube Shorts. "
            "IMPORTANT: Generate the title and description in Brazilian Portuguese (pt-BR), "
            "matching the language of the script. Tags can be in English (common YouTube search terms). "
            "Respond ONLY in JSON format."
        )

        prompt = f"""Generate YouTube metadata for this video.

Video topic: {plan.topic}
Hook: {plan.hook or ''}
Tone: {plan.tone}
Game: {game_name or 'N/A'}

Script excerpt:
{script_text}

Generate (title and description in Brazilian Portuguese, tags can be in English):
1. "title": A catchy title in pt-BR (max 100 characters). Use the game name if relevant. Include curiosity or emotion. NO clickbait that misleads.
2. "description": A description in pt-BR (max 1000 characters). First line = hook. Then 2-3 sentences expanding the topic. End with a call to action and relevant hashtags.
3. "tags": 8-12 lowercase tags (single words or short phrases), including the game name, topic keywords, and general gaming tags like "gameplay", "curiosidades", "gaming".

Respond as JSON: {{"title": "...", "description": "...", "tags": ["tag1", "tag2", ...]}}
"""

        try:
            kw = {"temperature": 0.8, "max_tokens": 1024}
            if model:
                kw["model"] = model
            data = self.llm.chat_json(system, prompt, **kw)

            title = str(data.get("title", "")).strip()[:100]
            description = str(data.get("description", "")).strip()[:5000]
            tags_raw = data.get("tags", [])
            if isinstance(tags_raw, list):
                tags = [str(t).strip().lower()[:30] for t in tags_raw if str(t).strip()]
            else:
                tags = []

            if not title:
                title = (plan.topic or "Gameplay Curiosidade")[:100]
            if not description:
                description = (script.final or "")[:5000]

            log.info(f"metadata_generator: LLM generated title='{title[:50]}...' tags={len(tags)}")
            return VideoMetadata(title=title, description=description, tags=tags)

        except (LLMError, Exception) as e:
            log.warning(f"metadata_generator: LLM failed ({e}), using fallback")
            return self._fallback(plan, script, game)

    def _fallback(
        self,
        plan: ContentPlan,
        script: Script,
        game: Optional[Game] = None,
    ) -> VideoMetadata:
        """Simple fallback: topic as title, script as description, game name as tag."""
        title = (plan.topic or "Gameplay Curiosidade")[:100]
        description = (script.final or "")[:5000]
        tags: list[str] = []
        if game:
            tags.append(game.canonical_name.lower())
        # Extract hashtags from description
        for word in description.split():
            if word.startswith("#") and len(word) <= 30:
                tags.append(word.lstrip("#").lower())
        return VideoMetadata(title=title, description=description, tags=tags)
