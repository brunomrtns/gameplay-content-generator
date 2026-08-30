"""API message dictionaries for localized backend responses.

Provides stable keys + localized messages. The frontend receives the key
and can localize independently; the PT-BR string is a fallback.

See MULTILINGUAL_PLAN.md §3.3 and §0.9.
"""

from __future__ import annotations

from typing import Any

# ── Stage labels (stable keys) ────────────────────────────────────────────────

STAGE_KEYS: dict[str, str] = {
    "content_planning": "stage.content_planning",
    "story_finding": "stage.story_finding",
    "editorial_planning": "stage.editorial_planning",
    "creative_engine": "stage.creative_engine",
    "script": "stage.script",
    "humanization": "stage.humanization",
    "script_review": "stage.script_review",
    "tts": "stage.tts",
    "gameplay_selection": "stage.gameplay_selection",
    "music_selection": "stage.music_selection",
    "render_plan": "stage.render_plan",
    "render": "stage.render",
    "qa": "stage.qa",
    "output": "stage.output",
    "metadata_generation": "stage.metadata_generation",
    "youtube_upload": "stage.youtube_upload",
    "presentation": "stage.presentation",
    "download": "stage.download",
    "confirm_download": "stage.confirm_download",
    "mapping": "stage.mapping",
}

# ── Worker activity keys (stable keys) ────────────────────────────────────────

WORKER_ACTIVITY_KEYS: dict[str, str] = {
    "generating_video": "worker.activity.generating_video",
    "uploading_video": "worker.activity.uploading_video",
    "kids_discovering_ideas": "worker.activity.kids_discovering_ideas",
    "kids_evaluating_idea": "worker.activity.kids_evaluating_idea",
    "kids_processing_media": "worker.activity.kids_processing_media",
    "kids_mapping_media": "worker.activity.kids_mapping_media",
    "identifying_game": "worker.activity.identifying_game",
    "mapping_gameplay": "worker.activity.mapping_gameplay",
    "downloading_document": "worker.activity.downloading_document",
    "indexing_document": "worker.activity.indexing_document",
    "collecting_rss": "worker.activity.collecting_rss",
    "enriching_game": "worker.activity.enriching_game",
    "cleaning_gameplay": "worker.activity.cleaning_gameplay",
    "cleaning_storage": "worker.activity.cleaning_storage",
    "downloading_file": "worker.activity.downloading_file",
    "synchronizing_file": "worker.activity.synchronizing_file",
}

# ── API error messages ────────────────────────────────────────────────────────

# Keys → {language: message}
API_MESSAGES: dict[str, dict[str, str]] = {
    "error.user_not_found": {
        "pt-BR": "Usuário não encontrado",
        "en": "User not found",
    },
    "error.cannot_delete_self": {
        "pt-BR": "Não é possível excluir a si mesmo",
        "en": "Cannot delete yourself",
    },
    "error.youtube_already_connected": {
        "pt-BR": "Canal do YouTube já conectado.",
        "en": "YouTube channel already connected.",
    },
    "error.youtube_not_connected": {
        "pt-BR": "Canal do YouTube não conectado.",
        "en": "YouTube channel not connected.",
    },
    "error.automation_not_found": {
        "pt-BR": "Automação não encontrada",
        "en": "Automation not found",
    },
    "error.gameplay_not_found": {
        "pt-BR": "Gameplay não encontrada",
        "en": "Gameplay not found",
    },
    "error.job_not_found": {
        "pt-BR": "Job não encontrado",
        "en": "Job not found",
    },
    "error.invalid_language": {
        "pt-BR": "Idioma inválido",
        "en": "Invalid language",
    },
    "error.unsupported_language": {
        "pt-BR": "Idioma não suportado pelo worker",
        "en": "Language not supported by worker",
    },
}

# ── Default names ─────────────────────────────────────────────────────────────

DEFAULT_AUTOMATION_NAME: dict[str, str] = {
    "pt-BR": "Minha Automação",
    "en": "My Automation",
}

DEFAULT_VIDEO_TITLE: dict[str, str] = {
    "pt-BR": "Vídeo #{id}",
    "en": "Video #{id}",
}


def get_message(key: str, language: str = "pt-BR", **kwargs: Any) -> str:
    """Get a localized API message by key.

    Falls back to pt-BR when the language is not available.
    Supports ``str.format(**kwargs)`` interpolation.
    """
    messages = API_MESSAGES.get(key)
    if messages is None:
        return key
    msg = messages.get(language) or messages.get("pt-BR") or key
    if kwargs:
        try:
            return msg.format(**kwargs)
        except (KeyError, IndexError):
            return msg
    return msg


def get_stage_key(stage: str) -> str:
    """Get the stable i18n key for a pipeline stage."""
    return STAGE_KEYS.get(stage or "", stage or "")


def get_activity_key(activity: str) -> str:
    """Get the stable i18n key for a worker activity."""
    return WORKER_ACTIVITY_KEYS.get(activity, activity)
