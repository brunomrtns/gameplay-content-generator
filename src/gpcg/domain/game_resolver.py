"""Game resolver — hierarchical game identification in 3 layers (V2).

L1: Deterministic — filename parsing + slug/alias registry match (O(log n)).
L2: Prior — historical association of capture_source → game.
L3: VLM — sample frames + gemma3:12b multimodal identification.

V2 changes (ARCHITECTURE_V2.md §6.3):
- L1 now uses the game_aliases table (indexed on LOWER(alias)) and Game.slug
  instead of scanning all games and checking JSON aliases.
- L3 builds the candidate catalog from canonical_name + game_aliases table.
- The resolver identifies existing games; the GameRegistry decides create/reuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gpcg.domain.filename_parser import ParsedFilename, parse_filename
from gpcg.domain.models import Game, GameAlias, GameplaySource, GameResolutionMethod
from gpcg.domain.slug_utils import normalize_name, slugify
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.infrastructure.media import extract_frames
from gpcg.logging import get_logger

log = get_logger(__name__)


@dataclass
class ResolutionResult:
    game_name: Optional[str]
    method: str  # GameResolutionMethod value
    confidence: float
    capture_source: Optional[str] = None
    notes: str = ""

    @property
    def needs_review(self) -> bool:
        return self.game_name is None or self.confidence < 0.6


def _normalize(s: str) -> str:
    return s.lower().strip()


def resolve_l1(parsed: ParsedFilename, session: Session) -> Optional[ResolutionResult]:
    """Layer 1 — deterministic: filename candidate vs. slug + alias registry.

    V2: Uses indexed lookups (O(log n)) instead of scanning all games.
    1. Search game_aliases by LOWER(alias) = normalize(candidate)
    2. Search Game by slug = slugify(candidate)
    3. Search Game by LOWER(canonical_name) = normalize(candidate)
    """
    if not parsed.candidate_game or parsed.is_capture_source_only:
        return None

    candidate = parsed.candidate_game
    normalized = normalize_name(candidate)
    slug = slugify(candidate)

    # 1. Search game_aliases (indexed on LOWER(alias))
    alias_row = session.execute(
        select(GameAlias).where(func.lower(GameAlias.alias) == normalized)
    ).scalar_one_or_none()
    if alias_row:
        game = session.get(Game, alias_row.game_id)
        if game:
            return ResolutionResult(
                game_name=game.canonical_name,
                method=GameResolutionMethod.deterministic.value,
                confidence=0.95,
                capture_source=parsed.capture_source,
                notes=f"matched alias '{candidate}' → game '{game.canonical_name}' (slug={game.slug})",
            )

    # 2. Search by slug (indexed)
    game = session.execute(
        select(Game).where(Game.slug == slug)
    ).scalar_one_or_none()
    if game:
        return ResolutionResult(
            game_name=game.canonical_name,
            method=GameResolutionMethod.deterministic.value,
            confidence=0.95,
            capture_source=parsed.capture_source,
            notes=f"matched slug '{slug}' → game '{game.canonical_name}'",
        )

    # 3. Search by canonical_name (case-insensitive)
    game = session.execute(
        select(Game).where(func.lower(Game.canonical_name) == normalized)
    ).scalar_one_or_none()
    if game:
        return ResolutionResult(
            game_name=game.canonical_name,
            method=GameResolutionMethod.deterministic.value,
            confidence=0.95,
            capture_source=parsed.capture_source,
            notes=f"matched canonical name '{candidate}'",
        )

    return None


def resolve_l2(parsed: ParsedFilename, session: Session) -> Optional[ResolutionResult]:
    """Layer 2 — prior: historical association of capture_source → game.

    If a capture_source (e.g. Yuzu) has only ever been associated with ONE game,
    use it as a weak prior. Never treat as truth.
    """
    if not parsed.capture_source:
        return None

    cs = parsed.capture_source.lower()
    # Find all sources with this capture_source that have a resolved game
    stmt = (
        select(GameplaySource, Game)
        .join(Game, GameplaySource.game_id == Game.id)
        .where(func.lower(GameplaySource.capture_source) == cs)
        .where(GameplaySource.resolution_method != GameResolutionMethod.unknown.value)
    )
    rows = session.execute(stmt).all()
    if not rows:
        return None

    # Count distinct games
    games = {row[1].canonical_name for row in rows}
    if len(games) == 1:
        game_name = next(iter(games))
        # Weak confidence — it's a prior, not truth
        return ResolutionResult(
            game_name=game_name,
            method=GameResolutionMethod.prior.value,
            confidence=0.5,
            capture_source=parsed.capture_source,
            notes=f"prior: capture_source '{cs}' previously only associated with '{game_name}'",
        )
    return None


def resolve_l3(
    video_path: Path,
    session: Session,
    llm: LLMClient,
    candidate_games: Optional[list[str]] = None,
) -> Optional[ResolutionResult]:
    """Layer 3 — VLM: sample frames + gemma3:12b identification.

    Expensive — only called when L1 and L2 fail.
    V2: builds candidate catalog from canonical_name + game_aliases table.
    """
    import tempfile

    # Build candidate catalog from registry
    if candidate_games is None:
        games = session.execute(select(Game)).scalars().all()
        candidate_games = [g.canonical_name for g in games]
        # Include aliases from game_aliases table (V2)
        alias_rows = session.execute(select(GameAlias)).scalars().all()
        candidate_games.extend(a.alias for a in alias_rows)

    catalog_str = ", ".join(candidate_games[:50]) if candidate_games else "(unknown — open set)"

    with tempfile.TemporaryDirectory(prefix="gpcg_vlm_") as tmp:
        frames = extract_frames(video_path, tmp, count=5)
        if len(frames) < 2:
            log.warning("VLM: not enough frames extracted, skipping")
            return None

        prompt = (
            "You are identifying which video game is shown in these screenshots.\n"
            f"Candidate games from the registry: {catalog_str}\n\n"
            "Look at the screenshots and identify the game. Respond as JSON:\n"
            '{"game": "<canonical name or empty if unknown>", "confidence": <0.0-1.0>, '
            '"reasoning": "<brief>"}\n'
            "If you cannot identify the game with confidence > 0.5, return empty game and low confidence."
        )
        try:
            data = llm.vision_json(frames, prompt, temperature=0.2, max_tokens=512)
        except LLMError as e:
            log.error(f"VLM identification failed: {e}")
            return None

    game_name = (data.get("game") or "").strip() if isinstance(data, dict) else ""
    confidence = float(data.get("confidence", 0.0)) if isinstance(data, dict) else 0.0

    if not game_name or confidence < 0.5:
        return ResolutionResult(
            game_name=None,
            method=GameResolutionMethod.vlm.value,
            confidence=confidence,
            notes=f"VLM uncertain: {data.get('reasoning', '') if isinstance(data, dict) else ''}",
        )

    return ResolutionResult(
        game_name=game_name,
        method=GameResolutionMethod.vlm.value,
        confidence=confidence,
        notes=f"VLM: {data.get('reasoning', '') if isinstance(data, dict) else ''}",
    )


def resolve(
    video_path: Path,
    filename: str,
    session: Session,
    llm: Optional[LLMClient] = None,
) -> ResolutionResult:
    """Full hierarchical resolution: L1 → L2 → L3.

    Returns the best result. If all layers fail, returns needs_review result.
    """
    parsed = parse_filename(filename)

    # L1
    r1 = resolve_l1(parsed, session)
    if r1 and not r1.needs_review:
        log.info(f"L1 deterministic: {r1.game_name} (conf={r1.confidence:.2f})")
        return r1

    # L2
    r2 = resolve_l2(parsed, session)
    if r2 and not r2.needs_review:
        log.info(f"L2 prior: {r2.game_name} (conf={r2.confidence:.2f})")
        return r2

    # If L1 gave a weak candidate but no registry match, keep it as a fallback
    # before resorting to VLM
    weak_candidate = r1 or r2

    # L3 — expensive
    if llm is not None:
        try:
            r3 = resolve_l3(video_path, session, llm)
            if r3 and not r3.needs_review:
                log.info(f"L3 VLM: {r3.game_name} (conf={r3.confidence:.2f})")
                return r3
        except Exception as e:
            log.error(f"L3 VLM error: {e}")

    # Fall back to whatever we have
    if weak_candidate:
        return weak_candidate

    # Last resort: use the parsed candidate as a manual-review candidate
    if parsed.candidate_game and not parsed.is_capture_source_only:
        return ResolutionResult(
            game_name=parsed.candidate_game,
            method=GameResolutionMethod.unknown.value,
            confidence=0.3,
            capture_source=parsed.capture_source,
            notes="no registry match; needs manual confirmation",
        )

    return ResolutionResult(
        game_name=None,
        method=GameResolutionMethod.unknown.value,
        confidence=0.0,
        capture_source=parsed.capture_source,
        notes="could not identify game; needs manual review",
    )
