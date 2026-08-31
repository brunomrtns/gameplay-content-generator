"""Test that all prompt packs export the same constants (no silent fallback)."""

import importlib

import pytest

# The canonical constants that every language pack must export.
# When a new constant is added to pt_br/domains.games.prompts, it must also
# be added to en_us, zh_cn, and zh_tw — otherwise PromptRegistry silently
# falls back to pt-BR, generating content in the wrong language.
REQUIRED_CONSTANTS = [
    "BEAT_ORIENTED_PROMPT_TEMPLATE",
    "CONTENT_PLANNING_SYSTEM",
    "CRITIC_SYSTEM",
    "CURIOSITY_SCORER_SYSTEM",
    "DRAFT_SYSTEM",
    "FACT_EXTRACTOR_SYSTEM",
    "METADATA_SYSTEM",
    "OPTIMIZE_SYSTEM",
    "PLAN_DRAFT_SYSTEM",
    "PLANNER_SYSTEM",
    "REVISION_SYSTEM",
    "REWRITE_SYSTEM",
    "SECTION_CRITIC_SYSTEM",
    "STORY_FINDER_SYSTEM",
    "SYSTEM_PROMPT_TEMPLATE",
]

PACKS = [
    ("games", "pt_br"),
    ("games", "en_us"),
    ("games", "zh_cn"),
    ("games", "zh_tw"),
]


@pytest.mark.parametrize("domain,lang_pack", PACKS)
def test_pack_exports_all_constants(domain: str, lang_pack: str):
    """Every language pack must export all required prompt constants."""
    module = importlib.import_module(f"gpcg.i18n.prompts.{lang_pack}.{domain}_prompts")
    missing = [name for name in REQUIRED_CONSTANTS if not hasattr(module, name)]
    assert not missing, (
        f"Pack '{lang_pack}/{domain}' is missing constants: {missing}. "
        f"PromptRegistry will silently fall back to pt-BR for these prompts, "
        f"generating content in the wrong language."
    )


@pytest.mark.parametrize("domain,lang_pack", PACKS)
def test_pack_constants_are_non_empty_strings(domain: str, lang_pack: str):
    """Every prompt constant must be a non-empty string (not None or empty)."""
    module = importlib.import_module(f"gpcg.i18n.prompts.{lang_pack}.{domain}_prompts")
    for name in REQUIRED_CONSTANTS:
        value = getattr(module, name)
        assert isinstance(value, str) and len(value.strip()) > 0, (
            f"Pack '{lang_pack}/{domain}' constant '{name}' is empty or not a string. "
            f"This would cause an empty prompt to be sent to the LLM."
        )
