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

from gpcg.core.models import ContentPlan, Script
from gpcg.domains.games.models import Game
from gpcg.domains.games.prompts import METADATA_SYSTEM
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.i18n.prompt_adapter import adapt_system_prompt

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
        language_context=None,
    ) -> VideoMetadata:
        """Generate optimized metadata for a video.

        Args:
            plan: Content plan with topic, hook, tone.
            script: Final script text.
            game: Optional game (for game name in tags/title).
            model: Override LLM model (default: use LLMClient default).
            language_context: Optional LanguageContext. When provided, the
                title and description are generated in the target language
                (instead of the hardcoded pt-BR). Tags can still be in English.

        Returns:
            VideoMetadata with title (<=100 chars), description (<=5000 chars),
            and 5-15 tags.
        """
        game_name = game.canonical_name if game else ""
        script_text = (script.final or "")[:2000]  # Truncate for prompt size

        system = adapt_system_prompt(METADATA_SYSTEM, language_context)

        # Language-aware prompt: when a language_context is provided, instruct
        # the LLM to write the title/description in the target language.
        # Without a context, preserve the original pt-BR behavior.
        if language_context is not None:
            from gpcg.i18n.language_context import get_language_name
            lang_name = get_language_name(getattr(language_context, "language", "pt-BR"))
            lang_instruction = (
                f"Generate (title and description in {lang_name}, tags can be in English):\n"
                f'1. "title": A catchy title in {lang_name} (max 100 characters). '
                f"Use the game name if relevant. Include curiosity or emotion. "
                f"NO clickbait that misleads.\n"
                f'2. "description": A description in {lang_name} (max 1000 characters). '
                f"First line = hook. Then 2-3 sentences expanding the topic. "
                f"End with a call to action and relevant hashtags.\n"
                f'3. "tags": 8-12 lowercase tags (single words or short phrases), '
                f"including the game name, topic keywords, and general gaming tags "
                f'like "gameplay", "gaming".\n\n'
                f"Respond as JSON: "
                f'{{"title": "...", "description": "...", "tags": ["tag1", "tag2", ...]}}\n'
            )
        else:
            lang_instruction = (
                "Generate (title and description in Brazilian Portuguese, tags can be in English):\n"
                '1. "title": A catchy title in pt-BR (max 100 characters). Use the game name if relevant. Include curiosity or emotion. NO clickbait that misleads.\n'
                '2. "description": A description in pt-BR (max 1000 characters). First line = hook. Then 2-3 sentences expanding the topic. End with a call to action and relevant hashtags.\n'
                '3. "tags": 8-12 lowercase tags (single words or short phrases), including the game name, topic keywords, and general gaming tags like "gameplay", "curiosidades", "gaming".\n\n'
                'Respond as JSON: {"title": "...", "description": "...", "tags": ["tag1", "tag2", ...]}\n'
            )

        prompt = f"""Generate YouTube metadata for this video.

Video topic: {plan.topic}
Hook: {plan.hook or ''}
Tone: {plan.tone}
Game: {game_name or 'N/A'}

Script excerpt:
{script_text}

{lang_instruction}"""

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
                # Language-aware fallback title
                if language_context is not None and not language_context.is_default:
                    from gpcg.i18n.language_context import get_language_name
                    lang_name = get_language_name(language_context.language)
                    title = (plan.topic or f"{lang_name} Gameplay")[:100]
                else:
                    title = (plan.topic or "Gameplay Curiosidade")[:100]
            if not description:
                description = (script.final or "")[:5000]

            log.info(f"metadata_generator: LLM generated title='{title[:50]}...' tags={len(tags)}")
            return VideoMetadata(title=title, description=description, tags=tags)

        except (LLMError, Exception) as e:
            log.warning(f"metadata_generator: LLM failed ({e}), using fallback")
            return self._fallback(plan, script, game, language_context=language_context)

    def _fallback(
        self,
        plan: ContentPlan,
        script: Script,
        game: Optional[Game] = None,
        *,
        language_context=None,
    ) -> VideoMetadata:
        """Simple fallback: topic as title, script as description, game name as tag."""
        if language_context is not None and not language_context.is_default:
            from gpcg.i18n.language_context import get_language_name
            lang_name = get_language_name(language_context.language)
            title = (plan.topic or f"{lang_name} Gameplay")[:100]
        else:
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
