"""Humanization pass — breaks AI patterns and ensures orality (V2).

Stage: between `script` and `script_review`.
See docs/EDITORIAL_REFACTOR_PLAN_V2.md §4.3, Fase 4.

Responsibility: take a generated script and make it sound HUMAN:
  1. AI-ism detection: scan for known patterns (enumerations, excessive
     connectives, "você não vai acreditar", "e é aí que", repetitive structures)
  2. Rhythm variation: detect if all sentences have the same length; if so, vary
  3. Redundancy removal: detect unnecessary explanations ("ou seja",
     "em outras palavras", "isto significa que")
  4. Orality injection: add natural pauses, rephrase written constructions as
     spoken ones
  5. Pattern breaking: if 3+ sentences have the same structure, rephrase one
  6. Identification with ignorance: inject phrases that acknowledge the
     narrator also didn't know ("eu também não sabia", "demorei pra entender
     isso"). Corrects the Curse of Knowledge (Heath) — the AI doesn't remember
     what it was like to not know; humanization injects that identification.

Approach: HYBRID. Regex detects the patterns (fast, deterministic), LLM
corrects them (creative, contextual). The regex pass produces a list of
detected issues; the LLM pass receives the script + the detected issues and
produces a humanized version.

Feature flag: GPCG_HUMANIZATION_ENABLED (default: false).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from gpcg.config import get_settings
from gpcg.domain.creative_plan import VideoCreativePlan
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)


# ── Detected issues (regex pass) ─────────────────────────────────────────────


@dataclass
class HumanizationIssue:
    """A single AI pattern detected by the regex pass."""
    pattern_type: str  # ai_ism, redundancy, repetitive_structure, uniform_rhythm
    match: str  # the matched text
    location: str  # approximate location (sentence index or char offset)
    suggestion: str = ""  # how to fix it

    def to_dict(self) -> dict:
        return {
            "pattern_type": self.pattern_type,
            "match": self.match,
            "location": self.location,
            "suggestion": self.suggestion,
        }


@dataclass
class HumanizationResult:
    """The result of the humanization pass."""
    original: str = ""
    humanized: str = ""
    changes: list[str] = field(default_factory=list)
    detected_issues: list[HumanizationIssue] = field(default_factory=list)
    latency_ms: int = 0
    success: bool = True
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "humanized": self.humanized,
            "changes": self.changes,
            "detected_issues": [i.to_dict() for i in self.detected_issues],
            "latency_ms": self.latency_ms,
            "success": self.success,
            "error": self.error,
        }

    @classmethod
    def empty(cls, error: str = "") -> HumanizationResult:
        return cls(success=False, error=error)

    @classmethod
    def from_dict(cls, d: dict) -> HumanizationResult:
        return cls(
            original=d.get("original", ""),
            humanized=d.get("humanized", ""),
            changes=d.get("changes", []),
            detected_issues=[HumanizationIssue(**i) for i in d.get("detected_issues", [])],
            latency_ms=int(d.get("latency_ms", 0)),
            success=bool(d.get("success", True)),
            error=str(d.get("error", "")),
        )


# ── Regex patterns for AI-ism detection ──────────────────────────────────────

# Common AI-ism phrases (pt-BR) — generic YouTube presenter tone
AI_ISM_PATTERNS = [
    (r"você não vai acreditar", "você não vai acreditar"),
    (r"prepare-se para", "prepare-se para"),
    (r"e é aí que (as coisas ficam|entra)", "e é aí que"),
    (r"j[áa] imaginou se", "já imaginou se"),
    (r"imagine um jogo onde", "imagine um jogo onde"),
    (r"o que torna isso (ainda mais|) interessante", "o que torna isso interessante"),
    (r"neste v[íi]deo (iremos|vamos)", "neste vídeo iremos"),
    (r"incr[íi]vel, n[ãa]o [ée]\?", "incrível, não é?"),
    (r"e [ée] por isso que", "é por isso que"),
    (r"mas espera, tem mais", "mas espera, tem mais"),
    (r"e a[íi] voc[êe] percebe que", "e aí você percebe que"),
    (r"isso [ée] mais .+ do que", "isso é mais X do que Y"),
    (r"[ée] como se .+ encontrasse", "é como se X encontrasse Y"),
]

# Redundancy markers — unnecessary explanations
REDUNDANCY_PATTERNS = [
    (r"ou seja,?\s", "ou seja"),
    (r"em outras palavras,?\s", "em outras palavras"),
    (r"isto [ée],?\sque", "isto é, que"),
    (r"quer dizer,?\sque", "quer dizer, que"),
    (r"ou melhor,?\s", "ou melhor"),
    (r"para quem n[ãa]o sabe,?\s", "para quem não sabe"),
]

# Identification with ignorance — phrases we WANT to inject (not detect)
# These are positive signals; if the script already has them, no need to add.
IGNORANCE_IDENTIFICATION_PHRASES = [
    "eu também não sabia",
    "demorei pra entender",
    "levei um tempo pra perceber",
    "confesso que não sabia",
    "pra ser sincero, não fazia ideia",
]


# ── Regex detector ───────────────────────────────────────────────────────────


def detect_ai_patterns(script: str) -> list[HumanizationIssue]:
    """Detect AI patterns in a script using regex (deterministic, fast).

    Returns a list of HumanizationIssue. Does NOT modify the script.
    """
    issues: list[HumanizationIssue] = []
    sentences = _split_sentences(script)

    # 1. AI-ism phrases
    for pattern, label in AI_ISM_PATTERNS:
        for match in re.finditer(pattern, script, re.IGNORECASE):
            issues.append(HumanizationIssue(
                pattern_type="ai_ism",
                match=match.group(),
                location=f"char {match.start()}",
                suggestion=f"Remove or rephrase the AI-ism '{label}' — it sounds generic.",
            ))

    # 2. Redundancy markers
    for pattern, label in REDUNDANCY_PATTERNS:
        for match in re.finditer(pattern, script, re.IGNORECASE):
            issues.append(HumanizationIssue(
                pattern_type="redundancy",
                match=match.group(),
                location=f"char {match.start()}",
                suggestion=f"Remove the redundant '{label}' — it over-explains.",
            ))

    # 3. Repetitive sentence structures (3+ sentences starting with the same word)
    if len(sentences) >= 3:
        starts = [s.strip().split()[0].lower() if s.strip().split() else "" for s in sentences]
        from collections import Counter
        start_counts = Counter(starts)
        for word, count in start_counts.items():
            if count >= 3 and word:
                issues.append(HumanizationIssue(
                    pattern_type="repetitive_structure",
                    match=f"{count} sentences start with '{word}'",
                    location="multiple sentences",
                    suggestion=f"Vary the sentence openings — {count} start with '{word}'.",
                ))

    # 4. Uniform rhythm (all sentences within 10% of the same length)
    if len(sentences) >= 4:
        lengths = [len(s) for s in sentences if s.strip()]
        if lengths:
            avg = sum(lengths) / len(lengths)
            if avg > 0:
                variance = max(abs(l - avg) for l in lengths) / avg
                if variance < 0.15:  # all within 15% of average
                    issues.append(HumanizationIssue(
                        pattern_type="uniform_rhythm",
                        match=f"all {len(lengths)} sentences ~{avg:.0f} chars",
                        location="entire script",
                        suggestion="Vary sentence lengths — mix short punchy ones with longer ones.",
                    ))

    # 5. Identification with ignorance — check if present (positive signal)
    has_ignorance_id = any(
        re.search(phrase, script, re.IGNORECASE) for phrase in IGNORANCE_IDENTIFICATION_PHRASES
    )
    if not has_ignorance_id:
        issues.append(HumanizationIssue(
            pattern_type="missing_ignorance_identification",
            match="(not present)",
            location="entire script",
            suggestion="Add a phrase acknowledging the narrator also didn't know "
                       "('eu também não sabia', 'demorei pra entender isso'). "
                       "This creates complicity with the viewer and corrects the Curse of Knowledge.",
        ))

    return issues


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences (simple — by . ! ? followed by space or end)."""
    # Split on sentence-ending punctuation followed by space or end of string
    parts = re.split(r'[.!?]+\s+', text)
    return [p.strip() for p in parts if p.strip()]


# ── LLM prompt for humanization ──────────────────────────────────────────────

HUMANIZATION_SYSTEM = """Você é uma CAMADA DE HUMANIZAÇÃO para roteiros de YouTube Shorts.
Seu trabalho: pegar um roteiro gerado por IA e fazê-lo soar HUMANO — como uma
pessoa real contando algo para um amigo, não como um apresentador de YouTube.

## O que você faz

1. REMOVA AI-isms: frases genéricas de IA ("você não vai acreditar",
   "prepare-se para", "e é aí que as coisas ficam interessantes", "já imaginou
   se", "neste vídeo iremos explorar"). REMOVA, não substitua por outra frase
   genérica. Silêncio > frase de IA.

2. REMOVA redundância: "ou seja", "em outras palavras", "isto significa que",
   "para quem não sabe". Se a frase já disse a coisa, não explique de novo.

3. VARIE o ritmo: se todas as frases têm o mesmo comprimento, misture curtas
   com longas. Frases curtas batem forte. Frases longas desenvolvem. O ritmo
   varia naturalmente na fala real.

4. QUEBRE padrões: se 3+ frases começam com a mesma palavra, reformule uma.
   A repetição soa robótica.

5. INJETE identificação com a ignorância: adicione UMA frase (não mais) que
   reconhece que o narrador também não sabia. Exemplos:
   - "eu também não sabia disso"
   - "demorei pra entender isso"
   - "confesso que não fazia ideia"
   Isso cria cumplicidade com o espectador e corrige a Maldição do Conhecimento
   (a IA não lembra como era não saber). NÃO force — só adicione se encaixar
   naturalmente.

## O que NÃO fazer

- NÃO reescreva o roteiro inteiro. Corrija os problemas detectados, mantenha o resto.
- NÃO adicione novos fatos. O roteiro é sobre um fato específico.
- NÃO mude a ideia central ou a estrutura narrativa.
- NÃO adicione humor se não houver. Silêncio > piada forçada.
- NÃO adicione mais de UMA frase de identificação com ignorância.

## Formato

Retorne APENAS JSON válido:
{
  "script": "<roteiro humanizado>",
  "changes": ["mudança 1", "mudança 2", ...]
}

As mudanças devem ser específicas: "removi 'prepare-se para'", "adicionei
'eu também não sabia'", "variou ritmo na frase X".
"""


# ── Humanizer ────────────────────────────────────────────────────────────────


class Humanizer:
    """Humanizes a script by breaking AI patterns and ensuring orality.

    Hybrid approach: regex detects patterns (fast, deterministic), LLM
    corrects them (creative, contextual).

    Gated by GPCG_HUMANIZATION_ENABLED. When off, returns the original script
    unchanged.
    """

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self.llm = llm or LLMClient()
        self.settings = get_settings()

    def humanize(
        self,
        script: str,
        creative_plan: Optional[VideoCreativePlan] = None,
    ) -> HumanizationResult:
        """Humanize a script.

        Args:
            script: the final narration script text
            creative_plan: the editorial plan (for context — tone, humor)

        Returns:
            HumanizationResult with humanized script + list of changes.
            On failure, returns the original script unchanged with success=False.
        """
        s = self.settings
        if not s.gpcg_humanization_enabled:
            return HumanizationResult(
                original=script, humanized=script, success=True,
                error="humanization disabled",
            )

        t0 = time.time()

        # Step 1: regex detection (deterministic)
        detected = detect_ai_patterns(script)
        if not detected:
            # No issues detected — no need for an LLM pass
            log.info("humanization: no AI patterns detected, skipping LLM pass")
            return HumanizationResult(
                original=script, humanized=script, detected_issues=[],
                latency_ms=int((time.time() - t0) * 1000), success=True,
            )

        # Step 2: LLM correction (creative, contextual)
        user_prompt = self._build_prompt(script, detected, creative_plan)

        try:
            data = self.llm.chat_json(
                system=HUMANIZATION_SYSTEM,
                prompt=user_prompt,
                model=s.gpcg_humanization_model or None,
                temperature=s.gpcg_humanization_temperature,
                max_tokens=s.gpcg_humanization_max_tokens,
            )
        except LLMError as e:
            log.error(f"humanization LLM failed: {e}")
            return HumanizationResult(
                original=script, humanized=script,
                detected_issues=[i.to_dict() for i in detected],
                latency_ms=int((time.time() - t0) * 1000),
                success=False, error=f"LLM error: {e}",
            )

        humanized = str(data.get("script", "")).strip() if isinstance(data, dict) else ""
        changes = data.get("changes", []) if isinstance(data, dict) else []
        if not isinstance(changes, list):
            changes = []
        if not humanized:
            # If the LLM returned empty, keep the original
            humanized = script
            changes = ["LLM returned empty script, keeping original"]

        latency_ms = int((time.time() - t0) * 1000)
        result = HumanizationResult(
            original=script,
            humanized=humanized,
            changes=[str(c) for c in changes],
            detected_issues=detected,
            latency_ms=latency_ms,
            success=True,
        )

        log.info(
            f"humanization: detected={len(detected)} issues, "
            f"changes={len(changes)}, latency={latency_ms}ms, "
            f"len {len(script)}→{len(humanized)}"
        )

        return result

    def _build_prompt(
        self,
        script: str,
        detected: list[HumanizationIssue],
        creative_plan: Optional[VideoCreativePlan],
    ) -> str:
        """Build the user prompt for the humanization LLM call."""
        parts = [
            "ROTEIRO PARA HUMANIZAR:",
            "---",
            script,
            "---",
            "",
            "PADRÕES DE IA DETECTADOS (corrija estes):",
        ]
        for issue in detected:
            parts.append(f"- [{issue.pattern_type}] {issue.match} ({issue.location})")
            if issue.suggestion:
                parts.append(f"  → {issue.suggestion}")
        parts.append("")

        if creative_plan is not None and creative_plan.success:
            parts.append(f"CONTEXTO: tom casual={creative_plan.tone.casual}, "
                         f"humor={creative_plan.humor.enabled}")
            parts.append("")

        parts.append("Humanize o roteiro. Corrija os padrões detectados. Retorne o JSON.")

        return "\n".join(parts)
