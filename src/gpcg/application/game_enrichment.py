"""Game enrichment — fetch external data and populate Game fields (V2).

Implements the enrich_game function per ARCHITECTURE_V2.md §6.5:
1. Wikidata (SPARQL/API) — identity: developer, publisher, franchise, genres, release_date
2. Wikipedia (REST API) — description + text for lore
3. LLM (Ollama) — lore_summary in pt-BR from Wikipedia text

All writes are atomic (single transaction at the end). Fetch failures
set enrichment_error; LLM failure is non-fatal (lore_summary stays NULL).

V2: `fetch_enrichment_data()` is a headless version (no DB session) that
returns an `EnrichmentResult` dataclass. Used by the remote worker to
fetch data locally and sync to VPS via API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.game_registry import add_alias
from gpcg.domain.models import Game
from gpcg.infrastructure.llm import LLMClient, LLMError
from gpcg.infrastructure.wikidata_client import WikidataClient, WikidataGameInfo
from gpcg.infrastructure.wikipedia_client import WikipediaClient, WikipediaArticle
from gpcg.logging import get_logger

log = get_logger(__name__)

CACHE_TTL_DAYS = 30
FETCH_RETRIES = 3
FETCH_BACKOFF = [1, 2, 4]  # seconds
LLM_RETRIES = 2


@dataclass
class EnrichmentResult:
    """Result of headless enrichment fetch (no DB needed)."""
    success: bool = False
    error: Optional[str] = None
    description: Optional[str] = None
    developer: Optional[str] = None
    publisher: Optional[str] = None
    franchise: Optional[str] = None
    genres: list = field(default_factory=list)
    themes: list = field(default_factory=list)
    lore_summary: Optional[str] = None
    release_date: Optional[datetime] = None
    external_ids: dict = field(default_factory=dict)
    aliases: list = field(default_factory=list)
    wikidata_qid: Optional[str] = None
    wikipedia_url: Optional[str] = None


def fetch_enrichment_data(
    game_name: str,
    *,
    llm: Optional[LLMClient] = None,
    wikidata: Optional[WikidataClient] = None,
    wikipedia: Optional[WikipediaClient] = None,
) -> EnrichmentResult:
    """Fetch enrichment data from Wikidata + Wikipedia + LLM (headless, no DB).

    This is the headless version of enrich_game — it fetches all external
    data and returns an EnrichmentResult without touching the database.
    The caller (remote worker) syncs the result to VPS via API.

    Args:
        game_name: Canonical game name to enrich
        llm: Optional LLMClient for lore generation
        wikidata: Optional WikidataClient (created if not provided)
        wikipedia: Optional WikipediaClient (created if not provided)

    Returns:
        EnrichmentResult with all fetched data, or error.
    """
    log.info(f"fetch_enrichment_data: starting for '{game_name}'")

    wd_client = wikidata or WikidataClient()
    wiki_client = wikipedia or WikipediaClient()

    # Step 1: Wikidata
    wd_info = _fetch_with_retry(lambda: wd_client.resolve_game(game_name), "Wikidata")
    if not wd_info:
        log.warning(f"fetch_enrichment_data: could not resolve '{game_name}' on Wikidata")
        return EnrichmentResult(success=False, error="could not resolve game on Wikidata")

    # Step 2: Wikipedia (using QID from Wikidata if available)
    wiki_article = _fetch_with_retry(
        lambda: wiki_client.get_article_for_game(game_name, qid=wd_info.qid),
        "Wikipedia",
    )
    if not wiki_article:
        log.warning(f"fetch_enrichment_data: could not fetch Wikipedia article for '{game_name}'")
        return EnrichmentResult(success=False, error="could not fetch Wikipedia article")

    # Step 3: LLM lore_summary (non-fatal if fails)
    lore_summary = None
    if wiki_article.extract:
        lore_summary = _generate_lore_with_retry(wiki_article.extract, game_name, llm)

    # Build result
    external_ids = {}
    if wd_info.steam_app_id:
        external_ids["steam"] = wd_info.steam_app_id
    if wd_info.qid:
        external_ids["wikidata"] = wd_info.qid
    if wiki_article.url:
        external_ids["wikipedia_url"] = wiki_article.url

    result = EnrichmentResult(
        success=True,
        description=wiki_article.description,
        developer=wd_info.developer,
        publisher=wd_info.publisher,
        franchise=wd_info.franchise,
        genres=wd_info.genres or [],
        lore_summary=lore_summary,
        release_date=wd_info.release_date,
        external_ids=external_ids,
        aliases=wd_info.aliases or [],
        wikidata_qid=wd_info.qid,
        wikipedia_url=wiki_article.url,
    )
    log.info(
        f"fetch_enrichment_data: success for '{game_name}' — "
        f"developer={result.developer}, franchise={result.franchise}, "
        f"genres={result.genres}, lore={'yes' if lore_summary else 'no'}"
    )
    return result


def enrich_game(
    session: Session,
    game_id: int,
    *,
    force: bool = False,
    llm: Optional[LLMClient] = None,
) -> bool:
    """Enrich a Game with data from Wikidata + Wikipedia + LLM.

    Args:
        session: SQLAlchemy session (transaction managed by caller or here)
        game_id: ID of the Game to enrich
        force: If True, ignore cache and re-fetch even if already enriched
        llm: Optional LLMClient for lore generation (created if not provided)

    Returns:
        True if enrichment succeeded (even partially), False on fetch failure.

    Side effects:
        - Sets Game.enriched_at on success
        - Sets Game.enrichment_error on fetch failure
        - Adds Wikidata aliases to game_aliases table
        - Caches results in Game.metadata_json.enrichment_cache
    """
    game = session.get(Game, game_id)
    if not game:
        log.error(f"enrich_game: Game #{game_id} not found")
        return False

    # Skip if already enriched and not forced
    if game.is_enriched and not force:
        log.info(f"enrich_game: Game '{game.canonical_name}' already enriched, skipping")
        return True

    # Check cache (unless force)
    cache = _get_cache(game)
    if not force and cache and _cache_valid(cache):
        log.info(f"enrich_game: using cached data for '{game.canonical_name}'")
        return _apply_cached_data(session, game, cache, llm)

    log.info(f"enrich_game: starting enrichment for '{game.canonical_name}' (id={game_id})")

    # Step 1: Wikidata
    wd_client = WikidataClient()
    wd_info = _fetch_with_retry(lambda: wd_client.resolve_game(game.canonical_name), "Wikidata")
    if not wd_info:
        _set_error(session, game, "could not resolve game on Wikidata")
        return False

    # Step 2: Wikipedia (using QID from Wikidata if available)
    wiki_client = WikipediaClient()
    wiki_article = _fetch_with_retry(
        lambda: wiki_client.get_article_for_game(game.canonical_name, qid=wd_info.qid),
        "Wikipedia",
    )
    # Wikipedia failure is fatal per §6.5 (no description without it)
    if not wiki_article:
        _set_error(session, game, "could not fetch Wikipedia article")
        return False

    # Step 3: LLM lore_summary (non-fatal if fails)
    lore_summary = None
    if wiki_article.extract:
        lore_summary = _generate_lore_with_retry(wiki_article.extract, game.canonical_name, llm)
        # LLM failure is non-fatal — lore stays NULL, enrichment still succeeds

    # --- Atomic write ---
    _apply_enrichment_data(session, game, wd_info, wiki_article, lore_summary)
    log.info(f"enrich_game: success for '{game.canonical_name}'")
    return True


def _fetch_with_retry(fetch_fn, source_name: str):
    """Retry a fetch operation with exponential backoff."""
    for attempt in range(FETCH_RETRIES):
        try:
            result = fetch_fn()
            if result:
                return result
            # None result might mean "not found" — don't retry on 404
            if attempt < FETCH_RETRIES - 1:
                log.warning(f"{source_name}: attempt {attempt + 1} returned no data, retrying...")
                time.sleep(FETCH_BACKOFF[attempt])
        except Exception as e:
            log.warning(f"{source_name}: attempt {attempt + 1} failed: {e}")
            if attempt < FETCH_RETRIES - 1:
                time.sleep(FETCH_BACKOFF[attempt])
    return None


def _generate_lore_with_retry(text: str, game_name: str, llm: Optional[LLMClient]) -> Optional[str]:
    """Generate lore_summary from Wikipedia text via LLM, with retries."""
    if llm is None:
        settings = get_settings()
        llm = LLMClient(
            base_url=settings.ollama_host,
            model=settings.gpcg_llm_model,
            timeout=settings.gpcg_llm_timeout,
        )

    prompt = (
        f"Você é um roteirista de vídeos sobre games. Resuma a história e o lore "
        f"do jogo '{game_name}' em português, em no máximo 3 parágrafos. "
        f"Foque no que é relevante para criar conteúdo editorial: contexto da história, "
        f"personagens principais, temas, e curiosidades notáveis. "
        f"Não liste mecânicas de gameplay.\n\n"
        f"Texto de referência (Wikipedia):\n{text[:4000]}\n\n"
        f"Resumo do lore:"
    )

    for attempt in range(LLM_RETRIES):
        try:
            response = llm.generate(prompt, temperature=0.4, max_tokens=1024)
            if response and len(response.strip()) > 50:
                return response.strip()
        except LLMError as e:
            log.warning(f"LLM lore generation attempt {attempt + 1} failed: {e}")
        except Exception as e:
            log.warning(f"LLM lore generation attempt {attempt + 1} error: {e}")

    log.info(f"LLM lore generation failed for '{game_name}' — lore_summary will be NULL (non-fatal)")
    return None


def _apply_enrichment_data(
    session: Session,
    game: Game,
    wd_info: WikidataGameInfo,
    wiki_article: WikipediaArticle,
    lore_summary: Optional[str],
) -> None:
    """Apply enrichment data to the Game in a single transaction."""
    # Wikidata fields
    if wd_info.developer:
        game.developer = wd_info.developer
    if wd_info.publisher:
        game.publisher = wd_info.publisher
    if wd_info.franchise:
        game.franchise = wd_info.franchise
    if wd_info.genres:
        game.genres = wd_info.genres
    if wd_info.release_date:
        game.release_date = wd_info.release_date
    if wd_info.steam_app_id:
        external_ids = dict(game.external_ids or {})
        external_ids["steam"] = wd_info.steam_app_id
        game.external_ids = external_ids
    if wd_info.qid:
        external_ids = dict(game.external_ids or {})
        external_ids["wikidata"] = wd_info.qid
        game.external_ids = external_ids

    # Wikipedia fields
    if wiki_article.description:
        game.description = wiki_article.description
    if wiki_article.url:
        external_ids = dict(game.external_ids or {})
        external_ids["wikipedia_url"] = wiki_article.url
        game.external_ids = external_ids

    # LLM field
    if lore_summary:
        game.lore_summary = lore_summary

    # Mark as enriched
    game.enriched_at = datetime.now(timezone.utc)
    game.enrichment_error = None

    # Add Wikidata aliases to game_aliases
    if wd_info.aliases:
        for alias in wd_info.aliases:
            if alias and alias.strip():
                add_alias(session, game.id, alias.strip(), source="wikidata")

    # Cache the results
    cache = {
        "wikidata": {
            "qid": wd_info.qid,
            "developer": wd_info.developer,
            "publisher": wd_info.publisher,
            "franchise": wd_info.franchise,
            "genres": wd_info.genres,
            "release_date": wd_info.release_date.isoformat() if wd_info.release_date else None,
            "aliases": wd_info.aliases,
            "steam_app_id": wd_info.steam_app_id,
        },
        "wikipedia": {
            "title": wiki_article.title,
            "description": wiki_article.description,
            "url": wiki_article.url,
            "extract_length": len(wiki_article.extract or ""),
        },
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata = dict(game.metadata_json or {})
    metadata["enrichment_cache"] = cache
    game.metadata_json = metadata

    session.flush()


def _get_cache(game: Game) -> Optional[dict]:
    """Get enrichment cache from Game.metadata_json."""
    metadata = game.metadata_json or {}
    return metadata.get("enrichment_cache")


def _cache_valid(cache: dict) -> bool:
    """Check if cache is within TTL."""
    cached_at = cache.get("cached_at")
    if not cached_at:
        return False
    try:
        cached_time = datetime.fromisoformat(cached_at)
        age_days = (datetime.now(timezone.utc) - cached_time).days
        return age_days < CACHE_TTL_DAYS
    except (ValueError, TypeError):
        return False


def _apply_cached_data(session: Session, game: Game, cache: dict, llm: Optional[LLMClient]) -> bool:
    """Apply cached enrichment data to a Game (no re-fetch)."""
    wd_cache = cache.get("wikidata", {})
    wiki_cache = cache.get("wikipedia", {})

    if wd_cache.get("developer"):
        game.developer = wd_cache["developer"]
    if wd_cache.get("publisher"):
        game.publisher = wd_cache["publisher"]
    if wd_cache.get("franchise"):
        game.franchise = wd_cache["franchise"]
    if wd_cache.get("genres"):
        game.genres = wd_cache["genres"]
    if wd_cache.get("release_date"):
        try:
            game.release_date = datetime.fromisoformat(wd_cache["release_date"])
        except (ValueError, TypeError):
            pass

    if wiki_cache.get("description"):
        game.description = wiki_cache["description"]

    game.enriched_at = datetime.now(timezone.utc)
    game.enrichment_error = None
    session.flush()
    return True


def _set_error(session: Session, game: Game, error_msg: str) -> None:
    """Set enrichment_error on a Game."""
    game.enrichment_error = error_msg
    session.flush()
    log.error(f"enrich_game: {error_msg} for '{game.canonical_name}'")
