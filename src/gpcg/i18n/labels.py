"""Localized labels for ChannelProfile context fields.

Used by ``ChannelProfile.to_prompt_context()`` and ``to_stage_context()``
to produce language-appropriate labels for LLM prompts.

See MULTILINGUAL_PLAN.md §12.1.
"""

from __future__ import annotations

# Labels by language. Each key is a BCP-47 tag.
# Missing languages fall back to pt-BR.
_LABELS: dict[str, dict[str, str]] = {
    "pt-BR": {
        "channel_description": "Descrição do canal",
        "niche": "Nicho",
        "target_audience": "Público-alvo",
        "tone_of_voice": "Tom de voz",
        "narrative_style": "Estilo de narrativa",
        "content_goals": "Objetivos",
        "special_rules": "Regras especiais",
        "niche_channel": "Nicho do canal",
        "content_goals_label": "Objetivos de conteúdo",
        "narrative_style_label": "Estilo narrativo",
    },
    "en-US": {
        "channel_description": "Channel description",
        "niche": "Niche",
        "target_audience": "Target audience",
        "tone_of_voice": "Tone of voice",
        "narrative_style": "Narrative style",
        "content_goals": "Content goals",
        "special_rules": "Special rules",
        "niche_channel": "Channel niche",
        "content_goals_label": "Content goals",
        "narrative_style_label": "Narrative style",
    },
    "zh-CN": {
        "channel_description": "频道描述",
        "niche": "细分领域",
        "target_audience": "目标受众",
        "tone_of_voice": "语气",
        "narrative_style": "叙事风格",
        "content_goals": "内容目标",
        "special_rules": "特殊规则",
        "niche_channel": "频道定位",
        "content_goals_label": "内容目标",
        "narrative_style_label": "叙事风格",
    },
    "zh-TW": {
        "channel_description": "頻道描述",
        "niche": "細分領域",
        "target_audience": "目標受眾",
        "tone_of_voice": "語氣",
        "narrative_style": "敘事風格",
        "content_goals": "內容目標",
        "special_rules": "特殊規則",
        "niche_channel": "頻道定位",
        "content_goals_label": "內容目標",
        "narrative_style_label": "敘事風格",
    },
    "zh": {
        "channel_description": "频道描述",
        "niche": "细分领域",
        "target_audience": "目标受众",
        "tone_of_voice": "语气",
        "narrative_style": "叙事风格",
        "content_goals": "内容目标",
        "special_rules": "特殊规则",
        "niche_channel": "频道定位",
        "content_goals_label": "内容目标",
        "narrative_style_label": "叙事风格",
    },
}


def get_label(key: str, language: str = "pt-BR") -> str:
    """Get a localized label for a context field.

    Falls back to pt-BR when the language is not available.
    """
    labels = _LABELS.get(language) or _LABELS.get("pt-BR")
    return labels.get(key, key)
