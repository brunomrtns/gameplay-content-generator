"""Knowledge endpoints — document indexing, game enrichment, content collection sync.

These endpoints receive results from the remote worker after it processes
``knowledge_index``, ``game_enrich``, and ``content_collect`` jobs locally
(where Ollama and Wikidata/Wikipedia are accessible without VPS IP blocks).
"""

from __future__ import annotations

import hashlib as _hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from gpcg.core.models import Document, Job, KnowledgeChunk
from gpcg.infrastructure.database import get_db

from gpcg.api.workers._common import worker_auth

log = logging.getLogger(__name__)
router = APIRouter(tags=["workers"])


# ── Document indexing result ─────────────────────────────────────────────────


class IndexingResultRequest(BaseModel):
    """Worker sends knowledge chunks back to VPS after indexing."""
    chunks: list[dict] = Field(default_factory=list)
    chunk_count: int = 0
    error: str = ""


@router.post("/documents/{doc_id}/indexing-result")
def submit_indexing_result(
    doc_id: int,
    req: IndexingResultRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends indexed knowledge chunks back to VPS.

    The worker has parsed the document (possibly with OCR), chunked it,
    generated embeddings (via Ollama), and now sends the chunks to VPS
    for storage. The VPS stores them in the knowledge_chunks table for
    retrieval during video generation.
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if req.error:
        doc.knowledge_status = "error"
        db.commit()
        log.warning(f"Document {doc_id} indexing failed: {req.error}")
        return {"ok": False, "error": req.error}

    # Delete existing chunks for this document (re-indexing case)
    db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc_id).delete()

    # Insert new chunks
    for chunk_data in req.chunks:
        chunk = KnowledgeChunk(
            user_id=doc.user_id,
            document_id=doc_id,
            game_id=doc.game_id,
            content=chunk_data["content"],
            embedding=chunk_data.get("embedding", []),
            chunk_index=chunk_data.get("chunk_index", 0),
            section=chunk_data.get("section"),
            char_start=chunk_data.get("char_start", 0),
            char_end=chunk_data.get("char_end", 0),
            embedding_model=chunk_data.get("embedding_model"),
        )
        db.add(chunk)

    # Update document status
    doc.knowledge_status = "indexed"
    doc.chunk_count = len(req.chunks)
    doc.text_extracted = True
    db.commit()

    log.info(f"Document {doc_id} indexed: {len(req.chunks)} chunks stored on VPS")
    return {"ok": True, "chunk_count": len(req.chunks)}


# ── V2: Enrichment + Content Collection sync endpoints ──────────────────────
# These endpoints receive results from the remote worker (PC local) after
# processing game_enrich and content_collect jobs locally (where Ollama
# and Wikidata/Wikipedia are accessible without VPS IP blocks).


class EnrichmentResultRequest(BaseModel):
    """Worker sends enriched Game data back to VPS."""
    description: Optional[str] = None
    developer: Optional[str] = None
    publisher: Optional[str] = None
    franchise: Optional[str] = None
    genres: list = []
    themes: list = []
    lore_summary: Optional[str] = None
    release_date: Optional[str] = None
    external_ids: dict = {}
    aliases: list[str] = []
    enrichment_error: Optional[str] = None


@router.post("/jobs/{job_id}/sync-enrichment")
def sync_enrichment_result(
    job_id: int,
    req: EnrichmentResultRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends enriched Game data back to VPS.

    The worker has fetched Wikidata/Wikipedia data and generated lore
    with the local LLM. Now it sends the structured fields to VPS for
    storage in the games table.
    """
    from gpcg.domains.games.models import Game
    from gpcg.domain.game_registry import add_alias

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.game_id:
        raise HTTPException(status_code=400, detail="Job has no game_id")

    game = db.get(Game, job.game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")

    if req.enrichment_error:
        game.enrichment_error = req.enrichment_error
        db.commit()
        log.warning(f"Enrichment failed for game '{game.canonical_name}': {req.enrichment_error}")
        return {"ok": False, "error": req.enrichment_error}

    # Apply enriched fields
    if req.description is not None:
        game.description = req.description
    if req.developer is not None:
        game.developer = req.developer
    if req.publisher is not None:
        game.publisher = req.publisher
    if req.franchise is not None:
        game.franchise = req.franchise
    if req.genres:
        game.genres = req.genres
    if req.themes:
        game.themes = req.themes
    if req.lore_summary is not None:
        game.lore_summary = req.lore_summary
    if req.release_date:
        try:
            from datetime import datetime
            game.release_date = datetime.fromisoformat(req.release_date)
        except (ValueError, TypeError):
            pass
    if req.external_ids:
        game.external_ids = req.external_ids

    game.enriched_at = datetime.now(timezone.utc)
    game.enrichment_error = None

    # Add new aliases discovered during enrichment
    for alias in req.aliases:
        try:
            add_alias(db, game.id, alias, alias_type="alternative", source="enrichment")
        except Exception:
            pass  # duplicate alias — skip

    db.commit()
    log.info(f"Game '{game.canonical_name}' enriched: developer={game.developer}, franchise={game.franchise}")
    return {"ok": True, "game_id": game.id, "enriched_at": game.enriched_at.isoformat()}


class KnowledgeItemSyncItem(BaseModel):
    """A single KnowledgeItem to sync from worker to VPS."""
    title: str
    content: str
    item_type: str
    source_type: str
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    published_at: Optional[str] = None
    editorial_score: float = 0.0
    franchise: Optional[str] = None
    developer: Optional[str] = None
    game_id: Optional[int] = None
    content_hash: str = ""
    tags: list = []


class ContentCollectionResultRequest(BaseModel):
    """Worker sends collected KnowledgeItems back to VPS."""
    items: list[KnowledgeItemSyncItem] = []
    cleaned_count: int = 0
    error: Optional[str] = None


@router.post("/jobs/{job_id}/sync-knowledge-items")
def sync_knowledge_items(
    job_id: int,
    req: ContentCollectionResultRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Worker sends collected KnowledgeItems back to VPS.

    The worker has collected RSS feeds, scored items with the local LLM,
    and now sends the structured items to VPS for storage.
    """
    from gpcg.core.models import KnowledgeItem, KnowledgeItemStatus

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if req.error:
        log.warning(f"Content collection job {job_id} failed: {req.error}")
        return {"ok": False, "error": req.error}

    # Dedup by content_hash — skip items that already exist
    existing_hashes = set()
    if req.items:
        hashes = [item.content_hash for item in req.items if item.content_hash]
        if hashes:
            existing = db.execute(
                select(KnowledgeItem.content_hash).where(
                    KnowledgeItem.content_hash.in_(hashes)
                )
            ).scalars().all()
            existing_hashes = set(existing)

    inserted = 0
    skipped = 0
    for item in req.items:
        if item.content_hash and item.content_hash in existing_hashes:
            skipped += 1
            continue
        ki = KnowledgeItem(
            game_id=item.game_id,
            is_public=True,  # RSS-collected items are shared across users
            title=item.title,
            content=item.content,
            item_type=item.item_type,
            source_type=item.source_type,
            source_url=item.source_url,
            source_name=item.source_name,
            editorial_score=item.editorial_score,
            status=KnowledgeItemStatus.fresh.value,
            franchise=item.franchise,
            developer=item.developer,
            content_hash=item.content_hash or _hashlib.sha256(
                f"{item.title}:{item.content}".encode()
            ).hexdigest()[:16],
            tags=item.tags,
        )
        if item.published_at:
            try:
                from datetime import datetime
                ki.published_at = datetime.fromisoformat(item.published_at)
            except (ValueError, TypeError):
                pass
        db.add(ki)
        inserted += 1

    db.commit()
    log.info(
        f"Content collection synced: {inserted} new items, {skipped} duplicates skipped, "
        f"{req.cleaned_count} old news cleaned (job #{job_id})"
    )

    # V3: Reconcile idea queues — if new KIs were inserted, auto-fill queues
    # for users with auto_fill_queue enabled. This runs on the VPS, not the
    # worker, so queues are filled immediately after collection completes.
    if inserted > 0:
        try:
            from gpcg.api.automation_routes import reconcile_all_users
            added = reconcile_all_users(db)
            if added > 0:
                log.info(f"Reconciliador: auto-filled {added} queue entries across all users after content collection")
        except Exception as e:
            log.warning(f"Reconciliador failed after content collection: {e}")

    return {"ok": True, "inserted": inserted, "skipped": skipped, "cleaned": req.cleaned_count}
