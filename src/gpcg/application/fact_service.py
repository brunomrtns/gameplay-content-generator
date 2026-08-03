"""Fact extraction service — LLM extracts structured facts from uploaded documents.

Pipeline:
  Document text → chunked → LLM extracts facts (claim, category, source_ref)
  → dedup against existing facts → quality/novelty scoring → persist.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.domain.models import Document, Fact, FactVerification, Game
from gpcg.infrastructure.document_parser import DocumentParseError, parse_document
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.logging import get_logger

log = get_logger(__name__)

# Chunk size tuned for local LLM context windows (~8B models)
CHUNK_CHARS = 4000
CHUNK_OVERLAP = 400


@dataclass
class ExtractedFact:
    category: str
    claim: str
    source_ref: str


def _chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    if len(text) <= size:
        return [text] if text.strip() else []
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def _fact_hash(claim: str) -> str:
    """Stable hash for dedup (normalized claim)."""
    norm = " ".join(claim.lower().split())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


SYSTEM_PROMPT = """You are a fact extractor for a gaming YouTube Shorts channel.
Given a chunk of text about a video game, extract interesting facts, curiosities,
easter eggs, trivia, development details, hidden mechanics, or little-known information
that would make an engaging ~60 second short video.

CRITICAL — LANGUAGE:
The source text may be in English or any other language. However, the extracted
facts (the "claim" field) MUST be written EXCLUSIVELY in Brazilian Portuguese
(pt-BR). Translate and adapt the information — never copy English phrases.

For each fact, provide:
- category: one of [curiosity, easter_egg, trivia, development, hidden_mechanic, history, character, bug, removed_content, reference, other]
- claim: a concise factual statement (1-3 sentences) in Portuguese (pt-BR)
- source_ref: where in the text this comes from (section/page if available)

CRITICAL — ANTI-PLAGIARISM:
The source documents are THIRD-PARTY content. You MUST rewrite every fact in your
own words. NEVER copy sentences, phrases, or distinctive word sequences verbatim
from the source. Reorganize the information, use synonyms, change sentence
structure, and reframe the narrative. The claim must convey the same FACT but
with completely original phrasing. If you cannot rewrite a fact without closely
mirroring the source, skip it.

Only extract facts that are:
1. Actually present in the text (do NOT invent)
2. Interesting enough for a Short
3. Tellable in ~60 seconds
4. REWRITTEN in original phrasing (no verbatim copying from source)

Return JSON: {"facts": [{"category": "...", "claim": "...", "source_ref": "..."}, ...]}
If no good facts in the chunk, return {"facts": []}."""


def extract_facts_from_document(
    session: Session,
    document: Document,
    llm: LLMClient,
) -> list[Fact]:
    """Extract facts from a document, dedup, score, and persist."""
    try:
        text = parse_document(document.file_path, document.file_type)
    except DocumentParseError as e:
        log.error(f"cannot parse document {document.filename}: {e}")
        document.text_extracted = False
        session.flush()
        return []

    document.text_extracted = True
    chunks = _chunk_text(text)
    log.info(f"document {document.filename}: {len(chunks)} chunk(s) to process")

    # Load existing fact hashes for dedup (same game_id, including NULL)
    if document.game_id is not None:
        existing = session.execute(
            select(Fact).where(Fact.game_id == document.game_id)
        ).scalars().all()
    else:
        # General facts (game_id IS NULL)
        existing = session.execute(
            select(Fact).where(Fact.game_id.is_(None))
        ).scalars().all()
    existing_hashes = {_fact_hash(f.claim) for f in existing}

    # Build prompt context — game name if available, else "general curiosity"
    game_name = document.game.canonical_name if document.game else "general curiosity (not game-specific)"

    created: list[Fact] = []
    for i, chunk in enumerate(chunks):
        prompt = f"Context: {game_name}\n\nText chunk {i + 1}/{len(chunks)}:\n\n{chunk}"
        try:
            data = llm.chat_json(SYSTEM_PROMPT, prompt, temperature=0.3, max_tokens=2048)
        except LLMError as e:
            log.error(f"LLM extraction failed for chunk {i + 1}: {e}")
            continue

        facts_raw = data.get("facts", []) if isinstance(data, dict) else []
        for fr in facts_raw:
            claim = (fr.get("claim") or "").strip()
            if not claim or len(claim) < 15:
                continue
            fh = _fact_hash(claim)
            if fh in existing_hashes:
                log.debug(f"dedup: skipping already-known fact")
                continue
            existing_hashes.add(fh)

            category = (fr.get("category") or "other").strip().lower()
            source_ref = (fr.get("source_ref") or document.filename).strip()

            fact = Fact(
                game_id=document.game_id,
                document_id=document.id,
                user_id=document.user_id,  # REFACTORY_V2: inherit owner from source document
                is_public=document.is_public,  # REFACTORY_V2: inherit visibility from source document
                category=category,
                claim=claim,
                source_ref=source_ref,
                verification=FactVerification.unverified.value,
                quality_score=0.0,  # scored separately
                novelty_score=0.0,
            )
            session.add(fact)
            session.flush()
            created.append(fact)
            log.info(f"  extracted fact #{fact.id}: [{category}] {claim[:60]}...")

    document.facts_extracted = True
    session.flush()
    log.info(f"document {document.filename}: extracted {len(created)} new fact(s)")
    return created


def score_facts(session: Session, game_id: int | None, llm: LLMClient) -> int:
    """Score unscored facts for a game (or general pool if game_id is None).

    Uses the LLM to evaluate editorial potential. Batches up to 10 facts.
    When curiosity scoring is enabled (GPCG_CURIOSITY_SCORING_ENABLED), also
    computes curiosity_score + sub-scores via the CuriosityScorer.
    """
    if game_id is not None:
        facts = session.execute(
            select(Fact).where(Fact.game_id == game_id).where(Fact.quality_score == 0.0)
        ).scalars().all()
        game = session.get(Game, game_id)
        game_name = game.canonical_name if game else "unknown"
    else:
        facts = session.execute(
            select(Fact).where(Fact.game_id.is_(None)).where(Fact.quality_score == 0.0)
        ).scalars().all()
        game_name = "general curiosity"

    if not facts:
        return 0

    scored = 0
    # Batch in groups of 10
    for i in range(0, len(facts), 10):
        batch = facts[i : i + 10]
        claims = [{"id": f.id, "claim": f.claim, "category": f.category} for f in batch]
        prompt = (
            f"Context: {game_name}\n\nEvaluate these facts for a YouTube Shorts channel. "
            "For each, give quality_score (editorial potential, 0-100) and novelty_score "
            "(how little-known, 0-100). Higher = better.\n\n"
            f"Facts: {claims}\n\n"
            "Return JSON: {\"scores\": [{\"id\": <int>, \"quality_score\": <0-100>, \"novelty_score\": <0-100>}]}"
        )
        try:
            data = llm.chat_json(
                "You are a YouTube Shorts content strategist.",
                prompt,
                temperature=0.4,
                max_tokens=1024,
            )
        except LLMError as e:
            log.error(f"scoring failed: {e}")
            continue
        scores = data.get("scores", []) if isinstance(data, dict) else []
        for s in scores:
            fid = s.get("id")
            fact = next((f for f in batch if f.id == fid), None)
            if fact is None:
                continue
            fact.quality_score = max(0.0, min(100.0, float(s.get("quality_score", 0))))
            fact.novelty_score = max(0.0, min(100.0, float(s.get("novelty_score", 0))))
            scored += 1
    session.flush()
    log.info(f"scored {scored}/{len(facts)} facts for '{game_name}'")

    # V2: also compute curiosity scores when enabled
    from gpcg.config import get_settings
    if get_settings().gpcg_curiosity_scoring_enabled:
        from gpcg.application.curiosity_scorer import CuriosityScorer
        curiosity_scorer = CuriosityScorer(llm=llm)
        curiosity_scored = curiosity_scorer.score_facts(session, game_id, llm=llm)
        log.info(f"curiosity-scored {curiosity_scored} facts for '{game_name}'")

    return scored
