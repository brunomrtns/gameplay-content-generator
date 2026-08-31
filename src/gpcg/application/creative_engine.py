"""Creative Engine — Qwen3-14B-powered creative material generation.

This is a dedicated creative layer that runs between content planning and
script generation. It decomposes creativity into discrete steps:

    FACT / TOPIC
        ↓
    CreativeEngine.generate_creative_material()
        ↓
    ┌─────────────────────────────┐
    │ hooks        (5 candidates) │
    │ angles       (5 candidates) │
    │ punchlines   (5 candidates) │
    │ observations (5 candidates) │
    └─────────────────────────────┘
        ↓
    ScriptService consumes the material when drafting the script.

Design goals:
- Reuses the existing LLMClient (Ollama) with a `model=` override — no new
  runtime, no new dependency.
- Style is a dataclass preset, not hardcoded prompt fragments. New presets
  can be added to CREATIVE_PRESETS without touching the engine.
- Robust JSON parsing: handles markdown fences, leading/trailing text, and
  partial JSON (reuses the same defensive strategy as LLMClient.chat_json).
- Fallback: when the engine fails and `gpcg_creative_engine_fallback=true`,
  the caller receives an empty CreativeMaterial and the pipeline continues
  with the legacy script path.
- Performance: a SINGLE LLM call produces all creative material in one
  structured response (hooks + angles + punchlines + observations). This
  keeps latency and VRAM pressure low for batch production.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, TYPE_CHECKING

from gpcg.config import get_settings
from gpcg.domains.games.prompts import BEAT_ORIENTED_PROMPT_TEMPLATE, SYSTEM_PROMPT_TEMPLATE
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

if TYPE_CHECKING:
    from gpcg.domain.creative_plan import HumorPlan, NarrativeBeat, VideoCreativePlan

log = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Style presets
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CreativeStyle:
    """A creative personality profile.

    Each float is 0.0–1.0 and is translated into natural-language
    instructions in the system prompt. New presets can be registered in
    CREATIVE_PRESETS without touching the engine code.

    ``label`` and ``description`` are PT-BR defaults. For multilingual
    support, ``localized_labels`` and ``localized_descriptions`` provide
    per-language overrides. Use ``get_localized_label()`` and
    ``get_localized_description()`` to access them.
    """

    name: str
    label: str
    energy: float = 0.7
    absurdity: float = 0.4
    sarcasm: float = 0.3
    informality: float = 0.8
    creativity: float = 0.8
    description: str = ""
    # Multilingual: per-language label/description overrides
    localized_labels: dict = field(default_factory=dict)
    localized_descriptions: dict = field(default_factory=dict)

    def get_localized_label(self, language: str = "pt-BR") -> str:
        return self.localized_labels.get(language, self.label)

    def get_localized_description(self, language: str = "pt-BR") -> str:
        return self.localized_descriptions.get(language, self.description)


CREATIVE_PRESETS: dict[str, CreativeStyle] = {
    "humor": CreativeStyle(
        name="humor",
        label="Humor brasileiro",
        energy=0.75,
        absurdity=0.45,
        sarcasm=0.35,
        informality=0.9,
        creativity=0.85,
        description="Humor espontâneo, observações engraçadas do cotidiano, "
        "analogias inesperadas. Sem piadas forçadas.",
        localized_labels={"en-US": "Brazilian humor"},
        localized_descriptions={"en-US": "Spontaneous humor, funny everyday observations, unexpected analogies. No forced jokes."},
    ),
    "absurd": CreativeStyle(
        name="absurd",
        label="Absurdo",
        energy=0.85,
        absurdity=0.9,
        sarcasm=0.4,
        informality=0.95,
        creativity=0.95,
        description="Levar as coisas ao extremo lógico. 'Isso não deveria "
        "existir.' Exagero consciente e comparações absurdas.",
        localized_labels={"en-US": "Absurd"},
        localized_descriptions={"en-US": "Take things to their logical extreme. 'This shouldn't exist.' Conscious exaggeration and absurd comparisons."},
    ),
    "sarcastic": CreativeStyle(
        name="sarcastic",
        label="Sarcástico",
        energy=0.6,
        absurdity=0.3,
        sarcasm=0.85,
        informality=0.85,
        creativity=0.75,
        description="Sarcasmo seco, observações irônicas, tom de 'óbvio que "
        "isso existe'. Não agressivo, apenas cínico.",
        localized_labels={"en-US": "Sarcastic"},
        localized_descriptions={"en-US": "Dry sarcasm, ironic observations, 'obviously this exists' tone. Not aggressive, just cynical."},
    ),
    "storytelling": CreativeStyle(
        name="storytelling",
        label="Narrativa",
        energy=0.55,
        absurdity=0.2,
        sarcasm=0.2,
        informality=0.7,
        creativity=0.85,
        description="Contar como uma história. Ritmo de narrativa, build-up, "
        "revelação. Foco no arco da informação.",
        localized_labels={"en-US": "Storytelling"},
        localized_descriptions={"en-US": "Tell it like a story. Narrative pacing, build-up, reveal. Focus on the information arc."},
    ),
    "curiosity": CreativeStyle(
        name="curiosity",
        label="Curiosidade pura",
        energy=0.65,
        absurdity=0.25,
        sarcasm=0.2,
        informality=0.75,
        creativity=0.7,
        description="Tom de 'olha isso que louco'. Foco em despertar "
        "curiosidade genuína, sem forçar humor.",
        localized_labels={"en-US": "Pure curiosity"},
        localized_descriptions={"en-US": "'Look at this, how crazy' tone. Focus on awakening genuine curiosity, without forcing humor."},
    ),
    "nostalgia": CreativeStyle(
        name="nostalgia",
        label="Nostalgia",
        energy=0.5,
        absurdity=0.15,
        sarcasm=0.15,
        informality=0.8,
        creativity=0.75,
        description="Tom de 'lembra disso?'. Apelo à memória afetiva, "
        "saudade de jogos antigos, contexto de época.",
        localized_labels={"en-US": "Nostalgia"},
        localized_descriptions={"en-US": "'Remember this?' tone. Appeal to emotional memory, nostalgia for old games, era context."},
    ),
    "dark_humor": CreativeStyle(
        name="dark_humor",
        label="Humor ácido",
        energy=0.7,
        absurdity=0.6,
        sarcasm=0.7,
        informality=0.9,
        creativity=0.85,
        description="Humor que beira o inadequado sem cruzar a linha. "
        "Observações ácidas sobre o jogo/realidade.",
        localized_labels={"en-US": "Dark humor"},
        localized_descriptions={"en-US": "Humor that borders on inappropriate without crossing the line. Acidic observations about the game/reality."},
    ),
    "high_energy": CreativeStyle(
        name="high_energy",
        label="Alta energia",
        energy=1.0,
        absurdity=0.5,
        sarcasm=0.3,
        informality=0.95,
        creativity=0.9,
        description="Ritmo acelerado, frases curtas, impacto. Estilo "
        "criador de conteúdo explosivo.",
        localized_labels={"en-US": "High energy"},
        localized_descriptions={"en-US": "Fast pace, short sentences, impact. Explosive content creator style."},
    ),
}


def get_style(name: str) -> CreativeStyle:
    """Resolve a style preset by name. Falls back to 'humor' if unknown."""
    return CREATIVE_PRESETS.get(name, CREATIVE_PRESETS["humor"])


# ─────────────────────────────────────────────────────────────────────────────
# Creative material (structured output)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CreativeMaterial:
    """Structured creative output consumed by the script generator."""

    hooks: list[str] = field(default_factory=list)
    angles: list[str] = field(default_factory=list)
    punchlines: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    style: str = ""
    model: str = ""
    latency_ms: int = 0
    success: bool = True
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def empty(cls, style: str = "", error: str = "") -> "CreativeMaterial":
        return cls(style=style, success=False, error=error)

    def summary(self) -> str:
        return (
            f"CreativeMaterial(style={self.style}, hooks={len(self.hooks)}, "
            f"angles={len(self.angles)}, punchlines={len(self.punchlines)}, "
            f"observations={len(self.observations)}, "
            f"latency_ms={self.latency_ms}, success={self.success})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# System prompt (the "Creative Bible")
# ─────────────────────────────────────────────────────────────────────────────


def _build_style_block(style: CreativeStyle) -> str:
    """Translate a CreativeStyle into natural-language instructions."""
    def level(v: float) -> str:
        if v >= 0.85:
            return "muito alto"
        if v >= 0.65:
            return "alto"
        if v >= 0.45:
            return "médio"
        if v >= 0.25:
            return "baixo"
        return "muito baixo"

    return (
        f"- Energia: {level(style.energy)} ({style.energy:.2f})\n"
        f"- Absurdo: {level(style.absurdity)} ({style.absurdity:.2f})\n"
        f"- Sarcasmo: {level(style.sarcasm)} ({style.sarcasm:.2f})\n"
        f"- Informalidade: {level(style.informality)} ({style.informality:.2f})\n"
        f"- Criatividade: {level(style.creativity)} ({style.creativity:.2f})\n"
        f"- Direção do estilo: {style.description}"
    )



# ─────────────────────────────────────────────────────────────────────────────
# CreativeEngine
# ─────────────────────────────────────────────────────────────────────────────


class CreativeEngineError(Exception):
    """Raised when the creative engine fails and fallback is disabled."""


class CreativeEngine:
    """Generates creative material (hooks, angles, punchlines, observations)
    using a dedicated local LLM (default: Qwen3-14B via Ollama).

    Reuses the existing LLMClient with a `model=` override — no new runtime.
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm  # may carry a custom text_model; we override per-call
        self.settings = get_settings()

    # ── Public API ───────────────────────────────────────────────────────────

    def generate_creative_material(
        self,
        *,
        topic: str,
        fact: str,
        context: str = "",
        style: Optional[CreativeStyle] = None,
        humor_plan: Optional["HumorPlan"] = None,
        model_override: Optional[str] = None,
        language_context=None,
    ) -> CreativeMaterial:
        """Generate hooks + angles + punchlines + observations in a single
        LLM call. Returns a CreativeMaterial (never raises — failures are
        captured in the result unless fallback is disabled).

        When `humor_plan` is provided (from the EditorialPlanner), the style
        is adjusted to match the plan's humor settings. If humor is disabled
        in the plan, the engine returns an empty material (no creative layer
        needed for serious videos).

        When `model_override` is provided (from the plan's model recommendation),
        it takes priority over the config default.
        """
        s = self.settings
        if not s.gpcg_creative_engine_enabled:
            return CreativeMaterial.empty(style="disabled", error="creative engine disabled")

        # If a humor plan is provided and humor is disabled, skip the engine
        if humor_plan is not None and not humor_plan.enabled:
            log.info("creative_engine skipped: humor disabled in plan")
            return CreativeMaterial.empty(style="no_humor", error="humor disabled in editorial plan")

        style = style or get_style(s.gpcg_creative_engine_style)

        # Adjust style based on humor plan if provided
        if humor_plan is not None and humor_plan.enabled:
            style = self._adjust_style_for_humor(style, humor_plan)

        model = model_override or s.gpcg_creative_engine_model
        temperature = s.gpcg_creative_engine_temperature
        max_tokens = s.gpcg_creative_engine_max_tokens

        system = SYSTEM_PROMPT_TEMPLATE.format(style_block=_build_style_block(style))
        from gpcg.i18n.prompt_adapter import adapt_system_prompt
        system = adapt_system_prompt(system, language_context)
        user_prompt = self._build_user_prompt(topic=topic, fact=fact, context=context)

        llm = self.llm or LLMClient()
        t0 = time.monotonic()
        try:
            data = llm.chat_json(
                system,
                user_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMError as e:
            return self._on_failure(style=style.name, model=model, error=str(e), t0=t0)

        latency_ms = int((time.monotonic() - t0) * 1000)
        material = self._parse_material(data, style=style.name, model=model, latency_ms=latency_ms)
        log.info(
            f"creative_engine: model={model} style={style.name} "
            f"latency_ms={latency_ms} hooks={len(material.hooks)} "
            f"angles={len(material.angles)} punchlines={len(material.punchlines)} "
            f"observations={len(material.observations)}"
        )
        return material

    def generate_beat_oriented_material(
        self,
        *,
        topic: str,
        fact: str,
        context: str = "",
        style: Optional[CreativeStyle] = None,
        humor_plan: Optional["HumorPlan"] = None,
        model_override: Optional[str] = None,
        narrative_beats: Optional[list["NarrativeBeat"]] = None,
        central_idea: str = "",
        language_context=None,
    ) -> CreativeMaterial:
        """Generate beat-oriented creative material (V2).

        Instead of generic hooks/angles/punchlines/observations, this method
        generates material ORIENTED BY NARRATIVE BEAT — 3 hooks for the
        "hook" beat, 3 angles for "development", 3 payoffs for "payoff",
        3 observations for commentary beats.

        Gated by GPCG_CREATIVE_ENGINE_BEAT_ORIENTED. When off, falls back
        to generate_creative_material (generic).

        Gate: only runs if humor.enabled OR tone.casual >= 0.5. For purely
        informative videos, the engine is skipped.
        """
        s = self.settings
        if not s.gpcg_creative_engine_enabled:
            return CreativeMaterial.empty(style="disabled", error="creative engine disabled")

        # V2 gate: skip for purely informative videos
        if humor_plan is not None and not humor_plan.enabled:
            # Check tone.casual — if we don't have the plan, we can't check,
            # so we rely on the caller to pass humor_plan. If humor is off
            # and we have no tone info, skip (conservative).
            log.info("creative_engine skipped: humor disabled (beat-oriented gate)")
            return CreativeMaterial.empty(style="no_humor", error="humor disabled (beat-oriented gate)")

        # If beat-oriented is disabled or no beats provided, fall back to generic
        if not getattr(s, "gpcg_creative_engine_beat_oriented", False) or not narrative_beats:
            return self.generate_creative_material(
                topic=topic, fact=fact, context=context, style=style,
                humor_plan=humor_plan, model_override=model_override,
                language_context=language_context,
            )

        style = style or get_style(s.gpcg_creative_engine_style)

        # Adjust style based on humor plan if provided
        if humor_plan is not None and humor_plan.enabled:
            style = self._adjust_style_for_humor(style, humor_plan)

        model = model_override or s.gpcg_creative_engine_model
        temperature = s.gpcg_creative_engine_temperature
        max_tokens = s.gpcg_creative_engine_max_tokens

        beats_block = self._format_beats(narrative_beats)
        system = BEAT_ORIENTED_PROMPT_TEMPLATE.format(
            style_block=_build_style_block(style),
            central_idea=central_idea or "(não especificada)",
            beats_block=beats_block,
        )
        from gpcg.i18n.prompt_adapter import adapt_system_prompt
        system = adapt_system_prompt(system, language_context)
        user_prompt = self._build_user_prompt(topic=topic, fact=fact, context=context)

        llm = self.llm or LLMClient()
        t0 = time.monotonic()
        try:
            data = llm.chat_json(
                system,
                user_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except LLMError as e:
            return self._on_failure(style=style.name, model=model, error=str(e), t0=t0)

        latency_ms = int((time.monotonic() - t0) * 1000)
        material = self._parse_material(data, style=style.name, model=model, latency_ms=latency_ms)
        log.info(
            f"creative_engine (beat-oriented): model={model} style={style.name} "
            f"latency_ms={latency_ms} beats={len(narrative_beats)} "
            f"hooks={len(material.hooks)} angles={len(material.angles)} "
            f"punchlines={len(material.punchlines)} observations={len(material.observations)}"
        )
        return material

    def _format_beats(self, beats: list["NarrativeBeat"]) -> str:
        """Format narrative beats for the beat-oriented prompt."""
        if not beats:
            return "(nenhum beat definido)"
        lines = []
        for beat in beats:
            lines.append(f"  - {beat.label}: {beat.description} (tipo: {beat.content_type})")
        return "\n".join(lines)

    def _adjust_style_for_humor(self, style: CreativeStyle, humor_plan: "HumorPlan") -> CreativeStyle:
        """Adjust a CreativeStyle based on the editorial HumorPlan.

        Low intensity → reduce absurdity, sarcasm, creativity
        Medium-low → moderate values
        Medium → keep style as-is
        High → boost absurdity and creativity
        """
        from dataclasses import replace
        from gpcg.domain.creative_plan import (
            HUMOR_INTENSITY_LOW, HUMOR_INTENSITY_MEDIUM_LOW,
            HUMOR_INTENSITY_MEDIUM, HUMOR_INTENSITY_HIGH,
        )

        if humor_plan.intensity == HUMOR_INTENSITY_LOW:
            return replace(style,
                absurdity=0.2, sarcasm=0.2, creativity=0.6,
                description=f"{style.description} [humor low: natural observations only]",
            )
        elif humor_plan.intensity == HUMOR_INTENSITY_MEDIUM_LOW:
            return replace(style,
                absurdity=0.3, sarcasm=0.4, creativity=0.7,
            )
        elif humor_plan.intensity == HUMOR_INTENSITY_HIGH:
            return replace(style,
                absurdity=0.7, sarcasm=0.6, creativity=0.95,
            )
        # Medium or unknown → keep style as-is
        return style

    # ── Granular helpers (decomposed creativity) ─────────────────────────────
    # These are convenience wrappers for testing / CLI smoke tests. The
    # production pipeline uses generate_creative_material() (single call).

    def generate_hooks(
        self, *, topic: str, fact: str, context: str = "", style: Optional[CreativeStyle] = None
    ) -> list[str]:
        m = self.generate_creative_material(topic=topic, fact=fact, context=context, style=style)
        return m.hooks

    def generate_punchlines(
        self, *, topic: str, fact: str, context: str = "", style: Optional[CreativeStyle] = None
    ) -> list[str]:
        m = self.generate_creative_material(topic=topic, fact=fact, context=context, style=style)
        return m.punchlines

    def generate_angles(
        self, *, topic: str, fact: str, context: str = "", style: Optional[CreativeStyle] = None
    ) -> list[str]:
        m = self.generate_creative_material(topic=topic, fact=fact, context=context, style=style)
        return m.angles

    # ── Internals ────────────────────────────────────────────────────────────

    def _build_user_prompt(self, *, topic: str, fact: str, context: str) -> str:
        parts = [f"TÓPICO: {topic}"]
        if context:
            parts.append(f"CONTEXTO: {context}")
        parts.append(f"FATO: {fact}")
        parts.append("Gere o material criativo agora.")
        return "\n".join(parts)

    def _parse_material(
        self, data: dict | list, *, style: str, model: str, latency_ms: int
    ) -> CreativeMaterial:
        """Defensive parsing — tolerate missing fields, non-list values,
        non-string items, and empty results."""
        if not isinstance(data, dict):
            return CreativeMaterial.empty(style=style, error=f"unexpected payload type: {type(data).__name__}")

        def _as_str_list(key: str) -> list[str]:
            raw = data.get(key, [])
            if not isinstance(raw, list):
                # Tolerate a single string instead of a list
                if isinstance(raw, str) and raw.strip():
                    return [raw.strip()]
                return []
            out: list[str] = []
            for item in raw:
                if isinstance(item, str):
                    s = item.strip()
                    if s:
                        out.append(s)
                elif item is not None:
                    out.append(str(item).strip())
            return out

        hooks = _as_str_list("hooks")
        angles = _as_str_list("angles")
        punchlines = _as_str_list("punchlines")
        observations = _as_str_list("observations")

        if not any([hooks, angles, punchlines, observations]):
            return CreativeMaterial.empty(
                style=style, error="all creative fields empty after parsing"
            )

        return CreativeMaterial(
            hooks=hooks,
            angles=angles,
            punchlines=punchlines,
            observations=observations,
            style=style,
            model=model,
            latency_ms=latency_ms,
            success=True,
        )

    def _on_failure(self, *, style: str, model: str, error: str, t0: float) -> CreativeMaterial:
        latency_ms = int((time.monotonic() - t0) * 1000)
        log.error(
            f"creative_engine FAILED: model={model} style={style} "
            f"latency_ms={latency_ms} error={error}"
        )
        if self.settings.gpcg_creative_engine_fallback:
            log.warning("creative_engine: fallback enabled — continuing without creative material")
            return CreativeMaterial.empty(style=style, error=error)
        raise CreativeEngineError(f"creative engine failed: {error}")
