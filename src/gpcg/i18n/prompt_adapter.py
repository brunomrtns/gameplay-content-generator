"""Adapt LLM system prompts for the target content language.

The stock prompts (DRAFT_SYSTEM, PLAN_DRAFT_SYSTEM, CREATIVE_ENGINE prompts,
etc.) all hardcode 'Brazilian Portuguese (pt-BR)' as the output language.
When generating content in another language, we prepend a CRITICAL language
directive that overrides any hardcoded pt-BR instructions. The LLM follows
the most prominent instruction, so a clear directive at the top takes
precedence over scattered pt-BR references in the prompt template.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gpcg.i18n.language_context import LanguageContext


def adapt_system_prompt(prompt: str, language_context) -> str:
    """Prepend a CRITICAL language override directive for non-pt-BR languages.

    For pt-BR (the default), returns the prompt unchanged.
    For other languages, prepends a strong directive that overrides any
    hardcoded pt-BR instructions in the prompt body.
    """
    if language_context is None:
        return prompt
    if getattr(language_context, "is_default", True):
        return prompt
    from gpcg.i18n.language_context import get_language_name
    name = get_language_name(language_context.language)
    directive = (
        f"CRITICAL — OUTPUT LANGUAGE OVERRIDE:\n"
        f"All narration, script, hooks, angles, punchlines, observations, "
        f"and ANY creative text MUST be written EXCLUSIVELY in {name} "
        f"({language_context.language}).\n"
        f"This overrides any other language instruction in this prompt.\n"
        f"Do NOT write in Portuguese, English, or any other language.\n"
        f"Use natural {name} phrasing, idioms, and cultural references.\n"
        f"If the input fact or context is in another language, TRANSLATE the "
        f"concept and write your output in {name}.\n\n"
    )
    return directive + prompt
