"""Internationalization (i18n) layer for GPCG.

Provides:
- LanguageContext: carries language/locale/model preferences through the pipeline.
- GenerationContext: extends LanguageContext with checkpoint-relevant fields.
- PromptRegistry: lazy-loaded, versioned prompt templates with fallback to pt-BR.
- API message dictionaries for localized backend responses.
"""
