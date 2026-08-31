"""Prompt registry — versioned, cached prompt templates with language fallback.

Prompts are stored as Python constants in ``gpcg/i18n/prompts/{lang}_{region}/``.
The registry loads them lazily, caches by (name, domain, language, version),
and falls back to ``pt_br`` when the requested language is not available.

See MULTILINGUAL_PLAN.md §12.2.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptTemplate:
    """A loaded prompt template with metadata."""

    name: str
    text: str
    language: str
    version: str
    output_schema: dict[str, Any]


class PromptRegistry:
    """Lazy-loaded, cached, versioned prompt registry."""

    _cache: dict[str, PromptTemplate] = {}

    @classmethod
    def get(
        cls,
        name: str,
        *,
        domain: str = "games",
        language: str = "pt-BR",
        version: str = "v1",
    ) -> PromptTemplate:
        """Get a prompt template by name, domain, language, and version.

        Falls back to pt-BR when the requested language pack is not available.
        """
        lang_key = language.lower().replace("-", "_")
        cache_key = f"{domain}:{name}:{lang_key}:{version}"

        if cache_key in cls._cache:
            return cls._cache[cache_key]

        # Try the requested language, then language-family fallback, then pt_br
        module = cls._load_module(domain, lang_key)
        resolved_lang = lang_key
        if module is None:
            # Language-family fallback (e.g. "zh" → "zh_cn")
            base = lang_key.split("_")[0]
            if base != lang_key:
                module = cls._load_module(domain, base)
                if module is not None:
                    resolved_lang = base
            # Variant fallback (e.g. "zh" → "zh_cn", "en" → "en_us")
            if module is None and base in ("zh", "en", "pt"):
                variant = f"{base}_cn" if base == "zh" else f"{base}_us" if base == "en" else f"{base}_br"
                module = cls._load_module(domain, variant)
                if module is not None:
                    resolved_lang = variant
        if module is None:
            module = cls._load_module(domain, "pt_br")
            resolved_lang = "pt_br"
        if module is None:
            raise KeyError(f"Prompt '{name}' not found in domain '{domain}' (tried {lang_key} and pt_br)")

        # Log WARNING when fallback to pt-BR occurs (silent fallback hides i18n bugs)
        if resolved_lang == "pt_br" and lang_key != "pt_br":
            log.warning(
                "PromptRegistry: prompt '%s' (domain='%s', language='%s') "
                "fell back to pt_br — content may be generated in Portuguese "
                "instead of the target language",
                name, domain, language,
            )

        raw = getattr(module, name, None)
        if raw is None:
            raise KeyError(f"Prompt '{name}' not found in module {module.__name__}")

        schema = getattr(module, f"{name}_SCHEMA", {"required": ["script"]})

        template = PromptTemplate(
            name=name,
            text=raw,
            language=language,
            version=version,
            output_schema=schema,
        )
        cls._cache[cache_key] = template
        return template

    @classmethod
    def _load_module(cls, domain: str, lang_key: str):
        """Try to import a prompt module for a given domain and language."""
        module_path = f"gpcg.i18n.prompts.{lang_key}.{domain}_prompts"
        try:
            return importlib.import_module(module_path)
        except ModuleNotFoundError:
            return None

    @classmethod
    def version_hash(cls, template: PromptTemplate) -> str:
        """Compute a short hash of the template content for A/B tracking."""
        return hashlib.sha1(
            f"{template.name}:{template.language}:{template.version}:{template.text}".encode()
        ).hexdigest()[:12]

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the cache (useful for tests)."""
        cls._cache.clear()
