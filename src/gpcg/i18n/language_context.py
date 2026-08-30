"""LanguageContext — carries language/locale/model preferences through the pipeline.

Instead of adding a ``language`` parameter to ~14 services, we pass a single
``LanguageContext`` object.  Adding a new field (e.g. ``currency_format``) means
editing this dataclass, not 14 function signatures.

See MULTILINGUAL_PLAN.md §12.1.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gpcg.core.models import ChannelProfile


# ── Language metadata ─────────────────────────────────────────────────────────

# BCP-47 → (ICU locale, TTS/Whisper short code, display name in English)
_LANGUAGE_META: dict[str, tuple[str, str, str]] = {
    "pt-BR": ("pt_BR", "pt", "Brazilian Portuguese"),
    "en-US": ("en_US", "en", "English (US)"),
    "zh-CN": ("zh_CN", "zh", "Simplified Chinese"),
    "zh-TW": ("zh_TW", "zh", "Traditional Chinese"),
    "zh": ("zh_CN", "zh", "Mandarin Chinese"),
}

DEFAULT_LANGUAGE = "pt-BR"


def get_locale(language: str) -> str:
    """Return the ICU/POSIX locale for a BCP-47 tag (e.g. ``pt-BR`` → ``pt_BR``)."""
    meta = _LANGUAGE_META.get(language)
    if meta:
        return meta[0]
    # Fallback: replace hyphen with underscore
    return language.replace("-", "_")


def get_tts_language(language: str) -> str:
    """Return the short TTS/Whisper code for a BCP-47 tag (e.g. ``pt-BR`` → ``pt``)."""
    meta = _LANGUAGE_META.get(language)
    if meta:
        return meta[1]
    # Fallback: take the part before the hyphen
    return language.split("-")[0].lower()


def get_language_name(language: str) -> str:
    """Return the English display name for a BCP-47 tag."""
    meta = _LANGUAGE_META.get(language)
    if meta:
        return meta[2]
    return language


def is_supported(language: str) -> bool:
    """Check whether a BCP-47 tag is in the known set."""
    return language in _LANGUAGE_META


# ── Character density ─────────────────────────────────────────────────────────

# Characters per second of clear TTS narration, by language family.
# Mandarin Chinese has much higher information density per character
# (~3.5 chars/sec) vs Latin scripts (~13-15 chars/sec).
# Used to compute target character counts for scripts.
_CHARS_PER_SECOND: dict[str, float] = {
    "pt": 13.0,
    "en": 15.0,
    "zh": 3.5,  # Mandarin (applies to zh-CN, zh-TW, zh)
}

# Default chars/sec when language is unknown
_DEFAULT_CPS = 13.0


def get_chars_per_second(language: str) -> float:
    """Return the approximate characters per second for TTS narration.

    Mandarin Chinese (~3.5) is much lower than Latin scripts (~13-15)
    because each character carries more phonetic and semantic information.
    """
    base = language.split("-")[0].lower()
    return _CHARS_PER_SECOND.get(base, _DEFAULT_CPS)


def get_target_char_range(duration_seconds: int, language: str) -> tuple[int, int]:
    """Return (min_chars, max_chars) for a target narration duration.

    For a 60-second video:
    - pt-BR: ~780-1020 chars (13 cps)
    - en-US: ~900-1170 chars (15 cps)
    - zh-CN/zh-TW/zh: ~180-240 chars (3.5 cps)
    """
    cps = get_chars_per_second(language)
    min_chars = int(duration_seconds * cps * 0.85)
    max_chars = int(duration_seconds * cps * 1.15)
    return (min_chars, max_chars)


def is_cjk(language: str) -> bool:
    """Check whether the language uses CJK characters (no word spaces)."""
    base = language.split("-")[0].lower()
    return base in ("zh", "ja", "ko")


# ── LanguageContext ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LanguageContext:
    """Immutable language/locale context passed through the generation pipeline.

    Created once per job from the ChannelProfile (or config defaults) and passed
    to every service that needs language awareness.
    """

    language: str = DEFAULT_LANGUAGE
    locale: str = "pt_BR"
    tts_language: str = "pt"
    prompt_version: str = "v1"
    model_preferences: dict[str, str] = field(default_factory=dict)

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def for_language(cls, language: str, *, prompt_version: str = "v1") -> "LanguageContext":
        """Create a LanguageContext for a specific language (convenience)."""
        return cls(
            language=language,
            locale=get_locale(language),
            tts_language=get_tts_language(language),
            prompt_version=prompt_version,
        )

    @classmethod
    def from_channel_profile(cls, profile: "ChannelProfile | None") -> "LanguageContext":
        """Build a LanguageContext from a ChannelProfile.

        Falls back to config defaults when the profile is None or when
        multilingual is disabled.
        """
        from gpcg.config import get_settings

        s = get_settings()

        # Kill switch: if multilingual is disabled, always return pt-BR
        if not getattr(s, "gpcg_multilingual_enabled", False):
            return cls()

        requested = getattr(profile, "target_language", None) or getattr(s, "gpcg_default_language", DEFAULT_LANGUAGE)

        # Allowlist check (config stores comma-separated string)
        allowed_raw = getattr(s, "gpcg_multilingual_languages", "pt-BR")
        allowed = [lang.strip() for lang in allowed_raw.split(",") if lang.strip()]
        if requested not in allowed:
            return cls()

        # Beta-user check (config stores comma-separated string of user IDs)
        beta_raw = getattr(s, "gpcg_multilingual_beta_users", "")
        beta_users = [int(uid.strip()) for uid in beta_raw.split(",") if uid.strip().isdigit()]
        if beta_users and profile is not None and profile.user_id not in beta_users:
            return cls()

        return cls(
            language=requested,
            locale=get_locale(requested),
            tts_language=get_tts_language(requested),
            prompt_version=getattr(profile, "prompt_version", "v1") if profile else "v1",
            model_preferences=getattr(profile, "model_preferences", None) or {} if profile else {},
        )

    @classmethod
    def from_artifacts(cls, artifacts: dict[str, Any]) -> "LanguageContext":
        """Reconstruct a LanguageContext from job.artifacts (for resume/checkpoint).

        Falls back to pt-BR when the artifacts don't contain a generation_context
        (backward compat with jobs created before multilingual).
        """
        ctx = artifacts.get("generation_context") or artifacts.get("language_context") or {}
        if not ctx:
            return cls()
        return cls(
            language=ctx.get("language", DEFAULT_LANGUAGE),
            locale=ctx.get("locale", "pt_BR"),
            tts_language=ctx.get("tts_language", "pt"),
            prompt_version=ctx.get("prompt_version", "v1"),
            model_preferences=ctx.get("model_preferences", {}),
        )

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def language_directive(self) -> str:
        """A natural-language directive injected into LLM prompts.

        Tells the model which language to write in and which locale to use
        for formatting dates, currencies, and numbers.
        """
        name = get_language_name(self.language)
        return (
            f"Content language: {self.language} ({name}).\n"
            f"Write ALL output (script, narration, titles, descriptions) in {name}.\n"
            f"Use locale {self.locale} for dates, currencies, and number formatting.\n"
            f"Do NOT mix languages."
        )

    @property
    def is_default(self) -> bool:
        """True when this context represents the default pt-BR."""
        return self.language == DEFAULT_LANGUAGE


# ── GenerationContext (extends LanguageContext for checkpoint safety) ─────────


@dataclass(frozen=True)
class GenerationContext(LanguageContext):
    """Extends LanguageContext with fields that affect checkpoint compatibility.

    When any of these fields change, checkpoints from a previous run must be
    invalidated to avoid mixing artifacts from different configurations.
    """

    tts_engine_version: str = "xtts-v2"
    llm_script_model: str = "llama3.1:8b"

    @classmethod
    def from_channel_profile(cls, profile: "ChannelProfile | None") -> "GenerationContext":
        """Build a GenerationContext from a ChannelProfile."""
        from gpcg.config import get_settings

        s = get_settings()
        base = LanguageContext.from_channel_profile(profile)
        return cls(
            language=base.language,
            locale=base.locale,
            tts_language=base.tts_language,
            prompt_version=base.prompt_version,
            model_preferences=base.model_preferences,
            tts_engine_version=getattr(s, "gpcg_tts_engine", "xtts-v2"),
            llm_script_model=s.gpcg_llm_model,
        )

    @classmethod
    def from_artifacts(cls, artifacts: dict[str, Any]) -> "GenerationContext":
        """Reconstruct from job.artifacts."""
        ctx = artifacts.get("generation_context") or {}
        if not ctx:
            return cls()
        return cls(
            language=ctx.get("language", DEFAULT_LANGUAGE),
            locale=ctx.get("locale", "pt_BR"),
            tts_language=ctx.get("tts_language", "pt"),
            prompt_version=ctx.get("prompt_version", "v1"),
            model_preferences=ctx.get("model_preferences", {}),
            tts_engine_version=ctx.get("tts_engine_version", "xtts-v2"),
            llm_script_model=ctx.get("llm_script_model", "llama3.1:8b"),
        )

    def is_compatible_with(self, stored: dict[str, Any]) -> bool:
        """Check whether stored checkpoint artifacts are compatible with this context.

        Returns False when language, prompt_version, tts_engine_version, or
        llm_script_model differ — the checkpoint must be invalidated.
        """
        if not stored:
            # No stored context = old job, assume compatible (backward compat)
            return True
        checks = [
            ("language", self.language),
            ("prompt_version", self.prompt_version),
            ("tts_engine_version", self.tts_engine_version),
            ("llm_script_model", self.llm_script_model),
        ]
        for key, expected in checks:
            actual = stored.get(key)
            if actual is not None and actual != expected:
                return False
        return True
