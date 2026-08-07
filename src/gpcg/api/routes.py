"""API routes — REST endpoints for the web UI.

Resources:
  /api/games          — list, create, detail
  /api/sources        — gameplay sources (inbox)
  /api/assets         — gameplay assets (clips)
  /api/documents      — upload + list docs per game
  /api/facts          — list facts per game, trigger extraction
  /api/content-plans  — list plans
  /api/scripts        — list scripts
  /api/jobs           — list, create, detail
  /api/videos         — list generated videos
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from gpcg.application.fact_service import (
    extract_facts_from_document,
    score_facts,
)
from gpcg.application.gameplay_asset_service import AssetCreate, GameplayAssetService
from gpcg.application.generation_service import GenerationService
from gpcg.application.ingestion_service import IngestionService
from gpcg.config import get_settings
from gpcg.domain.game_repository import get_or_create, list_all
from gpcg.domain.models import (
    ContentPlan,
    Document,
    Fact,
    Game,
    GameplayAsset,
    GameplayClipUsage,
    GameplayEvent,
    GameplaySource,
    IngestionStatus,
    Job,
    JobStatus,
    JobType,
    KnowledgeItem,
    KnowledgeItemStatus,
    Script,
    User,
    Video,
    VideoStatus,
)
from gpcg.infrastructure.auth import get_current_user
from gpcg.infrastructure.database import get_db, session_scope
from gpcg.infrastructure.document_parser import DocumentParseError, detect_type
from gpcg.infrastructure.llm import get_llm

log = logging.getLogger(__name__)
router = APIRouter(tags=["gpcg"])


# ── Games ─────────────────────────────────────────────────────────────────────


@router.get("/games")
def list_games(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # V2: games are now global (user_id deprecated). Show games that the user
    # has gameplay sources for, OR games still owned by this user (legacy).
    user_game_ids = db.execute(
        select(GameplaySource.game_id).where(
            GameplaySource.user_id == user.id,
            GameplaySource.game_id.is_not(None),
        ).distinct()
    ).scalars().all()
    legacy_game_ids = db.execute(
        select(Game.id).where(Game.user_id == user.id)
    ).scalars().all()
    visible_ids = set(user_game_ids) | set(legacy_game_ids)
    if not visible_ids:
        return []
    games = (
        db.query(Game)
        .filter(Game.id.in_(visible_ids))
        .order_by(Game.canonical_name)
        .all()
    )
    result = []
    for g in games:
        sources = db.execute(
            select(func.count()).select_from(GameplaySource).where(
                GameplaySource.game_id == g.id,
                GameplaySource.user_id == user.id,
            )
        ).scalar() or 0
        assets = db.execute(
            select(func.count())
            .select_from(GameplayAsset)
            .join(GameplaySource, GameplayAsset.source_id == GameplaySource.id)
            .where(GameplaySource.game_id == g.id, GameplaySource.user_id == user.id)
        ).scalar() or 0
        facts = db.execute(
            select(func.count()).select_from(Fact).where(Fact.game_id == g.id, Fact.user_id == user.id)
        ).scalar() or 0
        videos = db.execute(
            select(func.count()).select_from(Video).where(Video.game_id == g.id, Video.user_id == user.id)
        ).scalar() or 0
        result.append(
            {
                "id": g.id,
                "canonical_name": g.canonical_name,
                "slug": g.slug or "",
                "aliases": g.aliases,
                "platforms": g.platforms,
                "capture_sources": g.capture_sources,
                "counts": {"sources": sources, "assets": assets, "facts": facts, "videos": videos},
                "enrichment_state": g.enrichment_state if hasattr(g, "enrichment_state") else "pending",
                "created_at": g.created_at.isoformat() if g.created_at else None,
            }
        )
    return result


@router.post("/games")
def create_game(
    canonical_name: str = Form(...),
    aliases: str = Form(""),
    platforms: str = Form(""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    alias_list = [a.strip() for a in aliases.split(",") if a.strip()]
    platform_list = [p.strip() for p in platforms.split(",") if p.strip()]
    with session_scope() as session:
        game = Game(
            user_id=user.id,
            canonical_name=canonical_name,
            aliases=alias_list,
            platforms=platform_list,
        )
        session.add(game)
        session.flush()
        return {"id": game.id, "canonical_name": game.canonical_name}


@router.get("/games/{game_id}")
def get_game(game_id: int, db: Session = Depends(get_db)):
    g = db.get(Game, game_id)
    if g is None:
        raise HTTPException(404, "game not found")
    return {
        "id": g.id,
        "canonical_name": g.canonical_name,
        "aliases": g.aliases,
        "platforms": g.platforms,
        "capture_sources": g.capture_sources,
    }


# ── Sources (inbox) ───────────────────────────────────────────────────────────


@router.get("/sources")
def list_sources(
    game_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    include_public: bool = Query(False, description="Include public gameplays from other users"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # User's own sources (exclude deleted)
    stmt = select(GameplaySource).where(
        GameplaySource.user_id == user.id,
        GameplaySource.ingestion_status != IngestionStatus.deleted.value,
    ).order_by(GameplaySource.created_at.desc())
    if game_id is not None:
        stmt = stmt.where(GameplaySource.game_id == game_id)
    if status:
        stmt = stmt.where(GameplaySource.ingestion_status == status)
    sources = db.execute(stmt).scalars().all()

    # Public sources from other users (if requested)
    public_sources = []
    if include_public:
        pub_stmt = select(GameplaySource).where(
            GameplaySource.is_public == True,
            GameplaySource.user_id != user.id,
            GameplaySource.ingestion_status == IngestionStatus.ready.value,
            GameplaySource.ingestion_status != IngestionStatus.deleted.value,
        ).order_by(GameplaySource.created_at.desc())
        if game_id is not None:
            pub_stmt = pub_stmt.where(GameplaySource.game_id == game_id)
        public_sources = db.execute(pub_stmt).scalars().all()

    all_sources = sources + public_sources
    # Preload game names to avoid N+1 queries
    game_ids = {s.game_id for s in all_sources if s.game_id}
    games_map = {}
    if game_ids:
        games_map = {
            g.id: g.canonical_name
            for g in db.execute(select(Game).where(Game.id.in_(game_ids))).scalars().all()
        }
    return [
        {
            "id": s.id,
            "filename": s.filename,
            "game_id": s.game_id,
            "game_name": games_map.get(s.game_id) if s.game_id else None,
            "duration": s.duration,
            "width": s.width,
            "height": s.height,
            "fps": s.fps,
            "codec": s.codec,
            "has_audio": s.has_audio,
            "ingestion_status": s.ingestion_status,
            "processing_status": s.processing_status,
            "resolution_method": s.resolution_method,
            "resolution_confidence": s.resolution_confidence,
            "resolution_notes": s.resolution_notes,
            "capture_source": s.capture_source,
            "recorded_at": s.recorded_at.isoformat() if s.recorded_at else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "file_size": s.file_size,
            "downloaded_at": s.downloaded_at.isoformat() if s.downloaded_at else None,
            "analysis_status": s.analysis_status,
            "is_public": s.is_public,
            "owner_user_id": s.user_id,
            "is_own": s.user_id == user.id,
        }
        for s in all_sources
    ]


@router.get("/sources/{source_id}/events")
def get_source_events(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List mapping events for a gameplay source (timeline of what the VLM saw)."""
    source = db.query(GameplaySource).filter(
        GameplaySource.id == source_id,
        GameplaySource.user_id == user.id,
    ).first()
    if not source:
        raise HTTPException(404, "Source not found")

    events = db.query(GameplayEvent).filter(
        GameplayEvent.source_id == source_id,
    ).order_by(GameplayEvent.start_time.asc()).all()

    return {
        "source_id": source_id,
        "filename": source.filename,
        "duration": source.duration,
        "event_count": len(events),
        "events": [
            {
                "id": e.id,
                "start_time": e.start_time,
                "end_time": e.end_time,
                "event_type": e.event_type,
                "description": e.description,
                "characters": e.characters,
                "location": e.location,
                "tags": e.tags,
                "transcript": e.transcript,
                "visual_confidence": e.visual_confidence,
                "interesting_score": e.interesting_score,
            }
            for e in events
        ],
    }


@router.post("/sources/{source_id}/assign-game")
def assign_game_to_source(
    source_id: int,
    game_id: Optional[int] = Form(None),
    game_name: Optional[str] = Form(None),
    slug: Optional[str] = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually assign a game to a source.

    Accepts either:
    - game_id: an existing GPCG Game ID
    - game_name + slug: find-or-create a GPCG Game by slug (used when
      selecting from the IGDB catalog search)
    """
    s = db.get(GameplaySource, source_id)
    if s is None:
        raise HTTPException(404, "source not found")
    if s.user_id != user.id:
        raise HTTPException(403, "not your gameplay")

    if game_id is not None:
        g = db.get(Game, game_id)
        if g is None:
            raise HTTPException(404, "game not found")
    elif game_name and slug:
        # Find-or-create by slug
        g = db.execute(
            select(Game).where(Game.slug == slug)
        ).scalar_one_or_none()
        if g is None:
            g = Game(
                canonical_name=game_name,
                slug=slug,
                user_id=user.id,
            )
            db.add(g)
            db.flush()
    else:
        raise HTTPException(400, "Provide either game_id or game_name + slug")

    s.game_id = g.id
    s.resolution_method = "manual"
    s.resolution_confidence = 1.0
    s.resolution_notes = "manually assigned"
    s.ingestion_status = "ready"
    db.commit()
    return {"ok": True, "game_id": g.id, "game_name": g.canonical_name}


@router.post("/inbox/scan")
def scan_inbox(user: User = Depends(get_current_user)):
    """Trigger a one-shot inbox scan."""
    svc = IngestionService()
    n = svc.scan_once(user_id=user.id)
    return {"discovered": n}


@router.post("/gameplays/upload")
def upload_gameplay(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a gameplay recording file for the current user.

    Saves the file to temp_uploads/ (VPS temporary storage) and creates a
    GameplaySource record with processing_status=uploaded. The file stays
    on the VPS only until a worker downloads it (then it's deleted).

    A mapping job can be created separately via /api/gameplays/{id}/create-mapping-job.
    """
    import hashlib
    from gpcg.config import get_settings
    from gpcg.domain.models import GameplayProcessingStatus

    settings = get_settings()
    # Use temp_uploads_dir — files here are deleted after worker confirms download
    upload_dir = settings.temp_uploads_dir / f"user_{user.id}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Stream the upload to disk in chunks (NOT file.file.read() — that loads
    # the entire file into RAM, which OOMs on large gameplays and blocks the
    # response until the full body is buffered). Hash incrementally as we go.
    filename = file.filename or "upload.mp4"
    hasher = hashlib.sha256()
    file_size = 0
    # Temp file path — use hash prefix once known; first write to a .part file
    # then rename after the upload completes so partial uploads are ignored.
    tmp_path = upload_dir / f".{filename}.uploading.part"
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)  # 1 MiB chunks
                if not chunk:
                    break
                hasher.update(chunk)
                out.write(chunk)
                file_size += len(chunk)
    except Exception:
        # Clean up partial upload on any failure (client disconnect, OOM, etc.)
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise

    file_hash = hasher.hexdigest()

    # Check for duplicates
    existing = db.query(GameplaySource).filter(
        GameplaySource.user_id == user.id,
        GameplaySource.file_hash == file_hash,
    ).first()
    if existing:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(409, "Este arquivo já foi enviado")

    # Rename .part → final name now that the upload is complete + verified
    safe_name = f"{file_hash[:8]}_{filename}"
    file_path = upload_dir / safe_name
    tmp_path.rename(file_path)

    # storage_key is relative to temp_uploads_dir — opaque to the application
    storage_key = f"user_{user.id}/{safe_name}"

    # Create source record
    with session_scope() as session:
        source = GameplaySource(
            user_id=user.id,
            file_path=str(file_path),
            storage_key=storage_key,
            filename=filename,
            file_hash=file_hash,
            file_size=file_size,
            ingestion_status=IngestionStatus.discovered.value,
            processing_status=GameplayProcessingStatus.uploaded.value,
        )
        session.add(source)
        session.flush()

        # Trigger async probing (lightweight: FFprobe for media metadata only.
        # Heavy analysis (VLM, ASR) is done by the worker, not the VPS.)
        try:
            svc = IngestionService()
            svc._ingest_file(file_path, user_id=user.id)
        except Exception:
            pass  # Non-fatal — probing happens in background or on next scan

        return {
            "id": source.id,
            "filename": source.filename,
            "processing_status": source.processing_status,
            "ingestion_status": source.ingestion_status,
            "file_size": source.file_size,
        }


@router.patch("/gameplays/{source_id}/visibility")
def toggle_gameplay_visibility(
    source_id: int,
    is_public: bool = Query(..., description="Set to true to make public, false for private"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Toggle a gameplay source's visibility (public/private).

    Public gameplays can be used by other users as fallback when their own
    gameplays for the same game are exhausted.
    """
    source = db.get(GameplaySource, source_id)
    if source is None:
        raise HTTPException(404, "gameplay source not found")
    if source.user_id != user.id:
        raise HTTPException(403, "not your gameplay")

    source.is_public = is_public
    db.commit()
    log.info(f"gameplay #{source_id} visibility set to {'public' if is_public else 'private'} by user #{user.id}")
    return {
        "success": True,
        "source_id": source_id,
        "is_public": is_public,
    }


@router.delete("/sources/{source_id}")
def delete_gameplay_source(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a gameplay source and all associated data.

    Performs a soft-delete on the GameplaySource (marks as deleted) and
    removes all associated GameplayAssets, GameplayEvents, GameplayClipUsages,
    and GameplayEventEmbeddings from the DB. A cleanup_gameplay job is
    created so the worker deletes the physical files from its storage (HD).

    Safety checks:
    - Verifies ownership (user_id == requester)
    - Refuses if there's an active job (queued/running) using this source
    """
    from gpcg.domain.models import (
        GameplayClipUsage,
        GameplayEvent,
        GameplayEventEmbedding,
        JobPriority,
        WorkerCapability,
    )
    from gpcg.api.worker_routes import _generate_upload_token

    source = db.get(GameplaySource, source_id)
    if source is None:
        raise HTTPException(404, "gameplay source not found")
    if source.user_id != user.id:
        raise HTTPException(403, "not your gameplay")

    # Check for active jobs using this source
    active_job = db.query(Job).filter(
        Job.gameplay_source_id == source_id,
        Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
    ).first()
    if active_job:
        raise HTTPException(
            409,
            f"cannot delete: job #{active_job.id} is {active_job.status} using this gameplay",
        )

    # Collect event IDs for embedding cleanup
    event_ids = [e.id for e in db.query(GameplayEvent).filter(
        GameplayEvent.source_id == source_id
    ).all()]

    # Delete associated data
    db.query(GameplayAsset).filter(GameplayAsset.source_id == source_id).delete()
    db.query(GameplayClipUsage).filter(GameplayClipUsage.source_id == source_id).delete()
    if event_ids:
        db.query(GameplayEventEmbedding).filter(
            GameplayEventEmbedding.event_id.in_(event_ids)
        ).delete()
    db.query(GameplayEvent).filter(GameplayEvent.source_id == source_id).delete()

    # Soft-delete the source
    source.ingestion_status = IngestionStatus.deleted.value
    source.processing_status = None
    source.is_public = False

    # Create cleanup job so worker deletes physical files
    cleanup_job = Job(
        job_uuid=str(__import__("uuid").uuid4()),
        type=JobType.cleanup_gameplay.value,
        status=JobStatus.queued.value,
        stage="cleanup",
        gameplay_source_id=source.id,
        user_id=source.user_id,
        game_id=source.game_id,
        priority=JobPriority.normal.value,
        required_capabilities=[WorkerCapability.mapping.value],
        artifacts={"source_id": source_id, "filename": source.filename},
    )
    db.add(cleanup_job)
    db.flush()
    db.commit()

    log.info(
        f"gameplay #{source_id} deleted by user #{user.id} — "
        f"cleanup job #{cleanup_job.id} created for worker"
    )
    return {
        "ok": True,
        "source_id": source_id,
        "cleanup_job_id": cleanup_job.id,
    }


# ── Assets (clips) ────────────────────────────────────────────────────────────


@router.get("/assets")
def list_assets(game_id: int = Query(...), db: Session = Depends(get_db)):
    assets = GameplayAssetService.list_for_game(db, game_id)
    return [
        {
            "id": a.id,
            "source_id": a.source_id,
            "label": a.label,
            "start_sec": a.start_sec,
            "end_sec": a.end_sec,
            "duration": a.duration,
            "used_count": a.used_count,
        }
        for a in assets
    ]


@router.post("/assets")
def create_asset(
    source_id: int = Form(...),
    start_sec: float = Form(...),
    end_sec: float = Form(...),
    label: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        asset = GameplayAssetService.create(
            db,
            AssetCreate(source_id=source_id, start_sec=start_sec, end_sec=end_sec, label=label or None),
        )
        db.commit()
        return {"id": asset.id, "duration": asset.duration}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int, db: Session = Depends(get_db)):
    ok = GameplayAssetService.delete(db, asset_id)
    db.commit()
    if not ok:
        raise HTTPException(404, "asset not found")
    return {"ok": True}


# ── Documents (upload) ────────────────────────────────────────────────────────


@router.post("/documents/upload")
def upload_document(
    game_id: Optional[int] = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload a reference document (PDF/TXT/MD/DOCX).

    If game_id is provided, the document is tied to that game.
    If game_id is omitted/None, the document is a GENERAL curiosity source
    (used for the "random curiosities with gameplay background" format).
    """
    if game_id is not None:
        g = db.get(Game, game_id)
        if g is None:
            raise HTTPException(404, "game not found")
    try:
        ftype = detect_type(file.filename or "unknown.txt")
    except DocumentParseError as e:
        raise HTTPException(400, str(e))

    settings = get_settings()
    if game_id is not None:
        dest_dir = settings.docs_dir / f"game_{game_id}"
    else:
        dest_dir = settings.docs_dir / "general"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (file.filename or "document")
    # Avoid clobbering
    if dest.exists():
        dest = dest_dir / f"{dest.stem}_{dest.suffix}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    doc = Document(
        game_id=game_id,  # None for general curiosity docs
        user_id=user.id,  # REFACTORY_V2: owner-scoped document
        is_public=False,  # REFACTORY_V2: private by default (user can toggle)
        filename=file.filename or dest.name,
        file_path=str(dest),
        file_type=ftype,
        file_size=dest.stat().st_size,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return {"id": doc.id, "filename": doc.filename, "file_type": doc.file_type, "game_id": doc.game_id}


@router.get("/documents")
def list_documents(
    game_id: Optional[int] = Query(None),
    general: bool = Query(False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List documents. Either for a specific game, or general (general=true).

    REFACTORY_V2: filtered by visibility (own + shared pool + public of others).
    """
    from gpcg.domain.visibility import visible_to_user
    doc_vis = visible_to_user(Document.user_id, Document.is_public, user.id)
    if general:
        stmt = select(Document).where(Document.game_id.is_(None), doc_vis)
    elif game_id is not None:
        stmt = select(Document).where(Document.game_id == game_id, doc_vis)
    else:
        stmt = select(Document).where(doc_vis)
    docs = db.execute(stmt.order_by(Document.created_at.desc())).scalars().all()
    return [
        {
            "id": d.id,
            "game_id": d.game_id,
            "filename": d.filename,
            "file_type": d.file_type,
            "file_size": d.file_size,
            "text_extracted": d.text_extracted,
            "facts_extracted": d.facts_extracted,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in docs
    ]


@router.post("/documents/{doc_id}/extract-facts")
def extract_facts(doc_id: int, db: Session = Depends(get_db)):
    """Extract facts from a document using the LLM."""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    llm = get_llm()
    facts = extract_facts_from_document(db, doc, llm)
    # Score them (game_id may be None for general docs)
    if facts:
        score_facts(db, doc.game_id, llm)
    db.commit()
    return {"extracted": len(facts)}


# ── Facts ─────────────────────────────────────────────────────────────────────


@router.get("/facts")
def list_facts(
    game_id: Optional[int] = Query(None),
    general: bool = Query(False),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List facts. Either for a specific game, or general (general=true).

    REFACTORY_V2: filtered by visibility (own + shared pool + public of others).
    """
    from gpcg.domain.visibility import visible_to_user
    fact_vis = visible_to_user(Fact.user_id, Fact.is_public, user.id)
    if general:
        stmt = select(Fact).where(Fact.game_id.is_(None), fact_vis)
    elif game_id is not None:
        stmt = select(Fact).where(Fact.game_id == game_id, fact_vis)
    else:
        stmt = select(Fact).where(fact_vis)
    stmt = stmt.order_by((Fact.quality_score * Fact.novelty_score).desc())
    if category:
        stmt = stmt.where(Fact.category == category)
    facts = db.execute(stmt).scalars().all()
    return [
        {
            "id": f.id,
            "game_id": f.game_id,
            "category": f.category,
            "claim": f.claim,
            "source_ref": f.source_ref,
            "quality_score": f.quality_score,
            "novelty_score": f.novelty_score,
            "used_count": f.used_count,
            "verification": f.verification,
        }
        for f in facts
    ]


# ── Content Plans + Scripts ───────────────────────────────────────────────────


@router.get("/content-plans")
def list_plans(
    game_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # REFACTORY_V2: ContentPlan is a derived entity (always owned by the user
    # who created it). Filter by user_id — no shared pool for plans.
    stmt = (
        select(ContentPlan)
        .where(ContentPlan.user_id == user.id)
        .order_by(ContentPlan.created_at.desc())
    )
    if game_id is not None:
        stmt = stmt.where(ContentPlan.game_id == game_id)
    plans = db.execute(stmt).scalars().all()
    result = []
    for p in plans:
        scripts = db.execute(select(Script).where(Script.content_plan_id == p.id)).scalars().all()
        result.append(
            {
                "id": p.id,
                "game_id": p.game_id,
                "background_game_id": p.background_game_id,
                "background_game": p.background_game.canonical_name if p.background_game else None,
                "topic": p.topic,
                "hook": p.hook,
                "tone": p.tone,
                "energy": p.energy,
                "music_mood": p.music_mood,
                "visual_strategy": p.visual_strategy,
                "target_duration": p.target_duration,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "scripts": [
                    {
                        "id": s.id,
                        "status": s.status,
                        "char_count": s.char_count,
                        "final": s.final[:200],
                        "originality_score": s.originality_score,
                        "rewrite_count": s.rewrite_count,
                    }
                    for s in scripts
                ],
            }
        )
    return result


@router.get("/scripts/{script_id}")
def get_script(script_id: int, db: Session = Depends(get_db)):
    s = db.get(Script, script_id)
    if s is None:
        raise HTTPException(404, "script not found")
    return {
        "id": s.id,
        "draft": s.draft,
        "optimized": s.optimized,
        "final": s.final,
        "status": s.status,
        "char_count": s.char_count,
        "originality_score": s.originality_score,
        "originality_report": s.originality_report,
        "rewrite_count": s.rewrite_count,
    }


# ── Jobs ──────────────────────────────────────────────────────────────────────


@router.get("/jobs")
def list_jobs(
    status: Optional[str] = Query(None),
    limit: int = Query(50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Job.status == status)
    jobs = db.execute(stmt).scalars().all()
    return [
        {
            "id": j.id,
            "job_uuid": j.job_uuid,
            "type": j.type,
            "game_id": j.game_id,
            "status": j.status,
            "stage": j.stage,
            "progress": j.progress,
            "attempts": j.attempts,
            "error": j.error,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "updated_at": j.updated_at.isoformat() if j.updated_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
        }
        for j in jobs
    ]


@router.post("/jobs/generate")
def create_generation_job(
    game_id: int = Form(...),
    scene_duration: float = Form(0.0),
    video_format: str = Form(""),
    subtitle_font: str = Form(""),
    subtitle_font_size: int = Form(0),
    subtitle_color: str = Form(""),
    subtitle_outline_color: str = Form(""),
    subtitle_position: str = Form(""),
    subtitle_case: str = Form(""),
    voice: str = Form(""),
    creative_style: str = Form(""),
    transition_type: str = Form(""),
    transition_duration: float = Form(0.0),
    subtitle_box_enabled: Optional[bool] = Form(None),
    subtitle_box_color: str = Form(""),
    subtitle_box_padding: int = Form(0),
    subtitle_stroke_color: str = Form(""),
    subtitle_stroke_width: int = Form(0),
    subtitle_rounded_box: Optional[bool] = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a generation job for a game.

    Uses the user's automation config as defaults for subtitle/transition/voice
    settings. Explicit form params override the automation config.

    Optional customization params (override automation config defaults):
    - scene_duration: target duration of each gameplay scene in seconds (0 = auto)
    - video_format: "9:16", "16:9", "1:1", "4:5"
    - subtitle_font, subtitle_font_size, subtitle_color, subtitle_outline_color,
      subtitle_position ("top"/"middle"/"bottom"), subtitle_case ("upper"/"lower"/"none")
    - subtitle_box_enabled, subtitle_box_color, subtitle_box_padding,
      subtitle_stroke_color, subtitle_stroke_width, subtitle_rounded_box
    - transition_type (FFmpeg xfade name), transition_duration (seconds)
    - voice: filename of an uploaded voice (from /voices list), e.g. "bruno.wav"
    - creative_style: CreativeEngine style preset (e.g. "humor", "absurd",
      "storytelling"). Only used when GPCG_CREATIVE_ENGINE_ENABLED=true.
    """
    g = db.get(Game, game_id)
    if g is None:
        raise HTTPException(404, "game not found")
    settings = get_settings()

    # Load automation config as defaults (subtitle/transition/voice)
    from gpcg.domain.models import Automation
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    auto_cfg = auto.config or {} if auto else {}

    # Helper: use explicit param if non-empty/non-zero, else fall back to automation config
    def _pick(key: str, explicit, default=""):
        if explicit is not None and explicit != "" and explicit != 0 and explicit != 0.0:
            return explicit
        return auto_cfg.get(key, default)

    scene_duration = _pick("scene_duration", scene_duration, 0.0)
    video_format = _pick("video_format", video_format, "")
    subtitle_font = _pick("subtitle_font", subtitle_font, "")
    subtitle_font_size = _pick("subtitle_font_size", subtitle_font_size, 0)
    subtitle_color = _pick("subtitle_color", subtitle_color, "")
    subtitle_outline_color = _pick("subtitle_outline_color", subtitle_outline_color, "")
    subtitle_position = _pick("subtitle_position", subtitle_position, "")
    subtitle_case = _pick("subtitle_case", subtitle_case, "")
    creative_style = _pick("creative_style", creative_style, "")
    transition_type = _pick("transition_type", transition_type, "")
    transition_duration = _pick("transition_duration", transition_duration, 0.0)
    subtitle_box_color = _pick("subtitle_box_color", subtitle_box_color, "")
    subtitle_box_padding = _pick("subtitle_box_padding", subtitle_box_padding, 0)
    subtitle_stroke_color = _pick("subtitle_stroke_color", subtitle_stroke_color, "")
    subtitle_stroke_width = _pick("subtitle_stroke_width", subtitle_stroke_width, 0)

    # For boolean/None fields, use explicit if not None, else automation config
    if subtitle_box_enabled is None:
        subtitle_box_enabled = auto_cfg.get("subtitle_box_enabled")
    if subtitle_rounded_box is None:
        subtitle_rounded_box = auto_cfg.get("subtitle_rounded_box")

    # Voice: explicit param > automation config > none
    if not voice:
        voice = auto_cfg.get("voice", "")
    voice_path = ""
    if voice:
        # REFACTORY_V2: look in user's isolated directory first, then shared root
        user_voice = settings.voices_dir / f"user_{user.id}" / voice
        shared_voice = settings.voices_dir / voice
        if user_voice.exists():
            voice_path = str(user_voice)
        elif shared_voice.exists():
            voice_path = str(shared_voice)
        else:
            raise HTTPException(404, f"voice '{voice}' not found — upload it first")

    svc = GenerationService()
    job = svc.create_job(
        g.canonical_name,
        user_id=user.id,
        scene_duration=scene_duration,
        video_format=video_format,
        subtitle_font=subtitle_font,
        subtitle_font_size=subtitle_font_size,
        subtitle_color=subtitle_color,
        subtitle_outline_color=subtitle_outline_color,
        subtitle_position=subtitle_position,
        subtitle_case=subtitle_case,
        voice_path=voice_path,
        creative_style=creative_style,
        transition_type=transition_type,
        transition_duration=transition_duration,
        subtitle_box_enabled=subtitle_box_enabled,
        subtitle_box_color=subtitle_box_color,
        subtitle_box_padding=subtitle_box_padding,
        subtitle_stroke_color=subtitle_stroke_color,
        subtitle_stroke_width=subtitle_stroke_width,
        subtitle_rounded_box=subtitle_rounded_box,
    )
    return {"id": job.id, "status": job.status, "game": g.canonical_name}


@router.post("/jobs/curiosity")
def create_curiosity_job(
    background_game_id: int = Form(...),
    fact_id: Optional[int] = Form(None),
    scene_duration: float = Form(0.0),
    video_format: str = Form(""),
    subtitle_font: str = Form(""),
    subtitle_font_size: int = Form(0),
    subtitle_color: str = Form(""),
    subtitle_outline_color: str = Form(""),
    subtitle_position: str = Form(""),
    subtitle_case: str = Form(""),
    voice: str = Form(""),
    creative_style: str = Form(""),
    transition_type: str = Form(""),
    transition_duration: float = Form(0.0),
    subtitle_box_enabled: Optional[bool] = Form(None),
    subtitle_box_color: str = Form(""),
    subtitle_box_padding: int = Form(0),
    subtitle_stroke_color: str = Form(""),
    subtitle_stroke_width: int = Form(0),
    subtitle_rounded_box: Optional[bool] = Form(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a curiosity_short job: general curiosity fact + gameplay background.

    Uses the user's automation config as defaults for subtitle/transition/voice
    settings. Explicit form params override the automation config.

    The fact comes from the general pool (game_id=NULL).
    background_game_id is the game whose gameplay runs in the background.
    If fact_id is omitted, the system auto-picks the best general fact.
    """
    g = db.get(Game, background_game_id)
    if g is None:
        raise HTTPException(404, "background game not found")
    settings = get_settings()

    # Load automation config as defaults
    from gpcg.domain.models import Automation
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    auto_cfg = auto.config or {} if auto else {}

    def _pick(key: str, explicit, default=""):
        if explicit is not None and explicit != "" and explicit != 0 and explicit != 0.0:
            return explicit
        return auto_cfg.get(key, default)

    scene_duration = _pick("scene_duration", scene_duration, 0.0)
    video_format = _pick("video_format", video_format, "")
    subtitle_font = _pick("subtitle_font", subtitle_font, "")
    subtitle_font_size = _pick("subtitle_font_size", subtitle_font_size, 0)
    subtitle_color = _pick("subtitle_color", subtitle_color, "")
    subtitle_outline_color = _pick("subtitle_outline_color", subtitle_outline_color, "")
    subtitle_position = _pick("subtitle_position", subtitle_position, "")
    subtitle_case = _pick("subtitle_case", subtitle_case, "")
    creative_style = _pick("creative_style", creative_style, "")
    transition_type = _pick("transition_type", transition_type, "")
    transition_duration = _pick("transition_duration", transition_duration, 0.0)
    subtitle_box_color = _pick("subtitle_box_color", subtitle_box_color, "")
    subtitle_box_padding = _pick("subtitle_box_padding", subtitle_box_padding, 0)
    subtitle_stroke_color = _pick("subtitle_stroke_color", subtitle_stroke_color, "")
    subtitle_stroke_width = _pick("subtitle_stroke_width", subtitle_stroke_width, 0)
    if subtitle_box_enabled is None:
        subtitle_box_enabled = auto_cfg.get("subtitle_box_enabled")
    if subtitle_rounded_box is None:
        subtitle_rounded_box = auto_cfg.get("subtitle_rounded_box")
    if not voice:
        voice = auto_cfg.get("voice", "")
    voice_path = ""
    if voice:
        # REFACTORY_V2: look in user's isolated directory first, then shared root
        user_voice = settings.voices_dir / f"user_{user.id}" / voice
        shared_voice = settings.voices_dir / voice
        if user_voice.exists():
            voice_path = str(user_voice)
        elif shared_voice.exists():
            voice_path = str(shared_voice)
        else:
            raise HTTPException(404, f"voice '{voice}' not found — upload it first")

    svc = GenerationService()
    job = svc.create_curiosity_job(
        background_game_id,
        fact_id=fact_id,
        user_id=user.id,
        scene_duration=scene_duration,
        video_format=video_format,
        subtitle_font=subtitle_font,
        subtitle_font_size=subtitle_font_size,
        subtitle_color=subtitle_color,
        subtitle_outline_color=subtitle_outline_color,
        subtitle_position=subtitle_position,
        subtitle_case=subtitle_case,
        voice_path=voice_path,
        creative_style=creative_style,
        transition_type=transition_type,
        transition_duration=transition_duration,
        subtitle_box_enabled=subtitle_box_enabled,
        subtitle_box_color=subtitle_box_color,
        subtitle_box_padding=subtitle_box_padding,
        subtitle_stroke_color=subtitle_stroke_color,
        subtitle_stroke_width=subtitle_stroke_width,
        subtitle_rounded_box=subtitle_rounded_box,
    )
    return {
        "id": job.id,
        "status": job.status,
        "type": job.type,
        "background_game": g.canonical_name,
        "fact_id": fact_id,
    }


# ── Videos ────────────────────────────────────────────────────────────────────


@router.get("/videos")
def list_videos(
    game_id: Optional[int] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Video).where(Video.user_id == user.id).order_by(Video.created_at.desc())
    if game_id is not None:
        stmt = stmt.where(Video.game_id == game_id)
    videos = db.execute(stmt).scalars().all()
    result = []
    for v in videos:
        cp = db.get(ContentPlan, v.content_plan_id) if v.content_plan_id else None
        # Fetch social_title + creative_plan from the job that produced this video
        social_title = None
        social_description = None
        social_tags = None
        creative_plan_summary = None
        script_reviews = None
        if v.job_id:
            job = db.get(Job, v.job_id)
            if job and isinstance(job.artifacts, dict):
                social_title = job.artifacts.get("social_title")
                social_description = job.artifacts.get("social_description")
                social_tags = job.artifacts.get("social_tags")
                cp_art = job.artifacts.get("creative_plan")
                if isinstance(cp_art, dict):
                    creative_plan_summary = {
                        "video_type": cp_art.get("video_type"),
                        "humor_enabled": cp_art.get("humor", {}).get("enabled") if isinstance(cp_art.get("humor"), dict) else None,
                        "humor_intensity": cp_art.get("humor", {}).get("intensity") if isinstance(cp_art.get("humor"), dict) else None,
                        "narrative_beats": len(cp_art.get("narrative_beats", [])) if cp_art.get("narrative_beats") else 0,
                        "gameplay_strategy": cp_art.get("gameplay_strategy"),
                    }
                sr = job.artifacts.get("script_reviews")
                if isinstance(sr, list) and sr:
                    last = sr[-1]
                    if isinstance(last, dict):
                        script_reviews = {
                            "verdict": last.get("verdict"),
                            "score": last.get("score"),
                            "issues": last.get("issues"),
                        }

        # Game name
        game_name = None
        if v.game_id:
            g = db.get(Game, v.game_id)
            if g:
                game_name = g.canonical_name

        # KnowledgeItem (the idea that originated this video)
        ki_info = None
        ki = db.get(KnowledgeItem, v.knowledge_item_id) if v.knowledge_item_id else None
        if ki:
            ki_info = {
                "id": ki.id,
                "title": ki.title,
                "tags": ki.tags or [],
                "item_type": ki.item_type,
                "source_name": ki.source_name,
                "source_url": ki.source_url,
            }
        elif cp and cp.metadata_json:
            # Fallback: KI linked via ContentPlan metadata
            ki_id_meta = cp.metadata_json.get("knowledge_item_id")
            if ki_id_meta:
                ki_meta = db.get(KnowledgeItem, ki_id_meta)
                if ki_meta:
                    ki_info = {
                        "id": ki_meta.id,
                        "title": ki_meta.title,
                        "tags": ki_meta.tags or [],
                        "item_type": ki_meta.item_type,
                        "source_name": ki_meta.source_name,
                        "source_url": ki_meta.source_url,
                    }

        # Clips used (from GameplayClipUsage)
        clips_used = []
        clip_usages = db.execute(
            select(GameplayClipUsage).where(GameplayClipUsage.video_id == v.id)
        ).scalars().all()
        for cu in clip_usages:
            src = db.get(GameplaySource, cu.source_id)
            src_game_name = None
            if src and src.game_id:
                sg = db.get(Game, src.game_id)
                if sg:
                    src_game_name = sg.canonical_name
            clips_used.append({
                "source_id": cu.source_id,
                "source_name": src.filename if src else None,
                "source_game": src_game_name,
                "start_sec": round(cu.start_sec, 1),
                "end_sec": round(cu.end_sec, 1),
                "duration": round(cu.duration, 1),
            })

        # Script final text
        script_final = None
        if cp:
            script = db.execute(
                select(Script).where(Script.content_plan_id == cp.id).order_by(Script.created_at.desc()).limit(1)
            ).scalars().first()
            if script:
                script_final = script.final or script.optimized or script.draft

        result.append(
            {
                "id": v.id,
                "game_id": v.game_id,
                "game_name": game_name,
                "file_path": v.file_path,
                "storage_key": v.storage_key,
                "duration": v.duration,
                "width": v.width,
                "height": v.height,
                "qa_score": v.qa_score,
                "qa_passed": v.qa_report.get("passed", False) if v.qa_report else False,
                "status": v.status,
                "thumbnail_path": v.thumbnail_path,
                "topic": cp.topic if cp else None,
                "social_title": social_title,
                "social_description": social_description,
                "social_tags": social_tags,
                "youtube_url": v.youtube_url,
                "youtube_video_id": v.youtube_video_id,
                "created_at": v.created_at.isoformat() if v.created_at else None,
                # Rich metadata
                "knowledge_item": ki_info,
                "clips_used": clips_used,
                "script_final": script_final,
                "creative_plan": creative_plan_summary,
                "script_review": script_reviews,
            }
        )
    return result


@router.get("/videos/{video_id}/file")
def serve_video(
    video_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "video not found")
    # REFACTORY_V2: prefer storage_key (VPS-local path) over file_path
    # (which may point to the worker's local filesystem).
    settings = get_settings()
    if v.storage_key:
        p = settings.videos_dir / v.storage_key
        if p.exists():
            return _stream_video(p, request)
    # Fallback to file_path
    p = Path(v.file_path) if v.file_path else None
    if p and p.exists():
        return _stream_video(p, request)
    raise HTTPException(404, "video file missing")


def _stream_video(path: Path, request: Request) -> StreamingResponse:
    """Stream a video file with HTTP Range support for fast seeking.

    This allows the browser to request only the bytes it needs (e.g. for
    seeking), making video loading much faster — especially for large files.
    """
    file_size = path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        # Parse "bytes=start-end"
        import re
        match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            chunk_size = end - start + 1

            def chunk_generator():
                with open(path, "rb") as f:
                    f.seek(start)
                    remaining = chunk_size
                    while remaining > 0:
                        data = f.read(min(1024 * 1024, remaining))
                        if not data:
                            break
                        remaining -= len(data)
                        yield data

            return StreamingResponse(
                chunk_generator(),
                media_type="video/mp4",
                status_code=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(chunk_size),
                    "Cache-Control": "public, max-age=3600",
                },
            )

    # No range request — return full file with streaming
    def full_generator():
        with open(path, "rb") as f:
            while True:
                data = f.read(1024 * 1024)
                if not data:
                    break
                yield data

    return StreamingResponse(
        full_generator(),
        media_type="video/mp4",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.get("/videos/{video_id}/thumbnail")
def serve_thumbnail(video_id: int, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "video not found")

    # Try existing thumbnail path
    if v.thumbnail_path:
        p = Path(v.thumbnail_path)
        if p.exists():
            return FileResponse(str(p), media_type="image/jpeg")

    # Fallback: generate thumbnail on-demand from the video file
    video_path = None
    if v.storage_key:
        settings = get_settings()
        candidate = settings.videos_dir / v.storage_key
        if candidate.exists():
            video_path = candidate
    if not video_path and v.file_path:
        candidate = Path(v.file_path)
        if candidate.exists():
            video_path = candidate

    if video_path:
        try:
            from gpcg.infrastructure.media import generate_thumbnail, probe
            settings = get_settings()
            thumb_path = settings.videos_dir / f"{video_path.stem}_thumb.jpg"
            info = probe(video_path)
            at = min(1.0, max(0.1, (info.duration or 2.0) / 2))
            generate_thumbnail(video_path, thumb_path, at=at)
            # Persist the path so future requests don't regenerate
            v.thumbnail_path = str(thumb_path)
            db.commit()
            return FileResponse(str(thumb_path), media_type="image/jpeg")
        except Exception as e:
            log.warning(f"on-demand thumbnail generation failed for video #{video_id}: {e}")

    raise HTTPException(404, "thumbnail not available")


class VideoMetadataUpdate(BaseModel):
    """Request body for updating video metadata before publishing."""
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None


@router.put("/videos/{video_id}/metadata")
def update_video_metadata(
    video_id: int,
    body: VideoMetadataUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the social metadata (title, description, tags) of a video.

    These fields are stored in the job's artifacts and used when publishing
    to YouTube. Allows the user to edit them before publishing.
    """
    from sqlalchemy.orm.attributes import flag_modified

    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "video not found")
    if v.user_id != user.id:
        raise HTTPException(403, "not your video")

    job = db.get(Job, v.job_id) if v.job_id else None
    if not job:
        raise HTTPException(400, "video has no associated job to update")

    artifacts = dict(job.artifacts or {})
    if body.title is not None:
        artifacts["social_title"] = body.title.strip()
    if body.description is not None:
        artifacts["social_description"] = body.description.strip()
    if body.tags is not None:
        artifacts["social_tags"] = [t.strip().lstrip("#") for t in body.tags if t.strip()]

    job.artifacts = artifacts
    flag_modified(job, "artifacts")
    db.commit()

    return {
        "success": True,
        "social_title": artifacts.get("social_title"),
        "social_description": artifacts.get("social_description"),
        "social_tags": artifacts.get("social_tags"),
    }


class PublishVideoRequest(BaseModel):
    """Optional overrides for the publish endpoint."""
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None


@router.post("/videos/{video_id}/publish")
def publish_video(
    video_id: int,
    body: PublishVideoRequest = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Publish a video to YouTube via the google-integration service.

    The video must have a storage_key (uploaded by the worker) and the user
    must have a connected YouTube channel. On success, updates the Video
    with the YouTube URL and video ID.
    """
    from gpcg.domain.models import VideoStatus, Automation, Job
    from gpcg.infrastructure.google_integration_adapter import GoogleIntegrationAdapter
    from gpcg.config import get_settings

    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "video not found")
    if v.user_id != user.id:
        raise HTTPException(403, "not your video")
    if not v.storage_key:
        raise HTTPException(400, "video file not uploaded yet (no storage_key)")
    if v.status == VideoStatus.published.value:
        raise HTTPException(400, "video already published")

    settings = get_settings()

    # Resolve storage_key to absolute path on VPS
    video_path = settings.temp_uploads_dir / v.storage_key
    if not video_path.exists():
        # Try videos_dir
        video_path = settings.videos_dir / v.storage_key
    if not video_path.exists():
        raise HTTPException(400, f"video file not found on server: {v.storage_key}")

    # Get title/description/tags from job artifacts or content plan
    job = db.get(Job, v.job_id) if v.job_id else None
    artifacts = job.artifacts if job else {}
    title = artifacts.get("social_title", "")
    description = artifacts.get("social_description", "")
    tags = artifacts.get("social_tags", [])

    # V3: Apply user overrides from the request body (edited in the UI modal)
    if body:
        if body.title is not None:
            title = body.title.strip()
        if body.description is not None:
            description = body.description.strip()
        if body.tags is not None:
            tags = [t.strip().lstrip("#") for t in body.tags if t.strip()]
        # Also persist the overrides to job artifacts for future reference
        if job:
            from sqlalchemy.orm.attributes import flag_modified
            updated_artifacts = dict(artifacts)
            updated_artifacts["social_title"] = title
            updated_artifacts["social_description"] = description
            updated_artifacts["social_tags"] = tags
            job.artifacts = updated_artifacts
            flag_modified(job, "artifacts")
            db.commit()

    if not title:
        cp = db.get(ContentPlan, v.content_plan_id) if v.content_plan_id else None
        title = cp.topic if cp else f"Video #{v.id}"

    # Get upload settings from automation config
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    auto_config = auto.config if auto else {}
    privacy = auto_config.get("youtube_privacy", settings.gpcg_youtube_privacy)
    category_id = int(auto_config.get("youtube_category_id", settings.gpcg_youtube_category_id))

    # Mark as publishing
    v.status = VideoStatus.pending_approval.value
    db.commit()

    adapter = GoogleIntegrationAdapter(settings=settings)
    result = adapter.upload_to_youtube(
        video_path,
        title=title,
        description=description,
        tags=tags,
        user_id=user.id,
        privacy=privacy,
        category_id=category_id,
    )

    if result.success:
        v.youtube_url = result.youtube_url
        v.youtube_video_id = result.youtube_video_id
        v.status = VideoStatus.published.value
        db.commit()
        return {
            "success": True,
            "youtube_url": result.youtube_url,
            "youtube_video_id": result.youtube_video_id,
            "status": v.status,
        }
    else:
        v.status = VideoStatus.publish_failed.value
        db.commit()
        raise HTTPException(500, f"YouTube upload failed: {result.error}")


@router.delete("/videos/{video_id}")
def delete_video(
    video_id: int,
    release_clips: Optional[bool] = Query(None, description="Release gameplay clips. If omitted, auto-releases for pending videos, keeps for published."),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a video and optionally release its gameplay clips.

    REFACTORY_V2 lifecycle rules:
    - **Pending video** (not yet published to YouTube): clips are released
      **automatically** (no need to ask the user). The video never went public,
      so the segments are free to reuse.
    - **Published video** (already on YouTube): clips **remain used** by default.
      Deleting the local record doesn't un-publish the YouTube video, so the
      segments should stay marked as used. The user can explicitly pass
      ``release_clips=true`` to override this (future use case).

    Args:
        video_id: ID of the video to delete.
        release_clips: Explicit override. If None, the behavior depends on
            video status (auto-release for pending, keep for published).
            If True, always release. If False, never release.

    The video file and thumbnail are also deleted from disk.
    """
    from gpcg.application.clip_usage_service import release_clip_usage

    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "video not found")
    if v.user_id != user.id:
        raise HTTPException(403, "not your video")

    # REFACTORY_V2: determine clip release behavior based on video status
    is_published = v.status == VideoStatus.published.value
    if release_clips is None:
        # Auto: pending → release, published → keep
        should_release = not is_published
    else:
        should_release = release_clips

    # Delete video file from disk
    if v.file_path:
        try:
            p = Path(v.file_path)
            if p.exists():
                p.unlink()
        except Exception as e:
            log.warning(f"failed to delete video file {v.file_path}: {e}")

    # Delete thumbnail from disk
    if v.thumbnail_path:
        try:
            p = Path(v.thumbnail_path)
            if p.exists():
                p.unlink()
        except Exception as e:
            log.warning(f"failed to delete thumbnail {v.thumbnail_path}: {e}")

    # Also try storage_key-based path
    if v.storage_key:
        try:
            settings = get_settings()
            for d in [settings.videos_dir, settings.temp_uploads_dir]:
                p = d / v.storage_key
                if p.exists():
                    p.unlink()
                    break
        except Exception as e:
            log.warning(f"failed to delete video by storage_key {v.storage_key}: {e}")

    # Release clip usage based on lifecycle rules
    clips_released = 0
    if should_release:
        clips_released = release_clip_usage(db, video_id)

    # V2: Release KnowledgeItem (revert status from "used" to "fresh")
    # so the idea can be reused in a future video.
    ki_released = False
    if should_release and v.job_id:
        from gpcg.domain.models import Job, ContentPlan, KnowledgeItem, KnowledgeItemStatus
        job = db.get(Job, v.job_id)
        if job and job.content_plan_id:
            plan = db.get(ContentPlan, job.content_plan_id)
            # ContentPlan stores the KI reference in metadata_json
            if plan and plan.metadata_json:
                ki_id = plan.metadata_json.get("knowledge_item_id")
                if ki_id:
                    ki = db.get(KnowledgeItem, ki_id)
                    if ki and ki.status == KnowledgeItemStatus.used.value:
                        ki.status = KnowledgeItemStatus.fresh.value
                        ki_released = True

    # Delete the video record
    db.delete(v)
    db.commit()

    log.info(f"deleted video #{video_id} (release_clips={release_clips}, clips_released={clips_released}, ki_released={ki_released})")
    return {
        "success": True,
        "video_id": video_id,
        "clips_released": clips_released,
        "ki_released": ki_released,
    }


@router.post("/videos/{video_id}/regenerate")
def regenerate_video(
    video_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Regenerate a video from its original idea.

    This does NOT delete the original video — both coexist in the gallery.
    The flow:
    1. Find the KnowledgeItem (idea) that originated this video.
    2. Release the gameplay clips used by this video (so they're available
       for the regeneration).
    3. Revert the KI status from "used" to "fresh".
    4. Add the KI to the END of the user's idea queue (preserving any
       gameplay_preference from the original generation).

    The user can then reorder/approve the queue to trigger generation.
    """
    from gpcg.application.clip_usage_service import release_clip_usage
    from gpcg.api.knowledge_item_routes import _normalize_idea_queue, _queue_ki_ids
    from gpcg.domain.models import Automation
    from sqlalchemy.orm.attributes import flag_modified

    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "video not found")
    if v.user_id != user.id:
        raise HTTPException(403, "not your video")

    # Find the KnowledgeItem
    ki = None
    ki_id = None
    if v.knowledge_item_id:
        ki = db.get(KnowledgeItem, v.knowledge_item_id)
        ki_id = v.knowledge_item_id
    else:
        # Fallback: KI linked via ContentPlan metadata
        if v.job_id:
            job = db.get(Job, v.job_id)
            if job and job.content_plan_id:
                plan = db.get(ContentPlan, job.content_plan_id)
                if plan and plan.metadata_json:
                    ki_id_meta = plan.metadata_json.get("knowledge_item_id")
                    if ki_id_meta:
                        ki = db.get(KnowledgeItem, ki_id_meta)
                        ki_id = ki_id_meta

    if ki is None:
        raise HTTPException(400, "Este vídeo não tem uma ideia associada — não é possível regenerar")

    # Release clips used by this video
    clips_released = release_clip_usage(db, video_id)

    # Revert KI status to fresh
    ki_was_used = ki.status == KnowledgeItemStatus.used.value
    if ki_was_used:
        ki.status = KnowledgeItemStatus.fresh.value

    # Add KI to the end of the idea queue
    auto = db.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        raise HTTPException(404, "Automation not found — cannot access idea queue")

    config = dict(auto.config or {})
    queue = _normalize_idea_queue(config.get("idea_queue", []))
    existing_ids = _queue_ki_ids(queue)

    # Try to recover the original gameplay_preference from the job artifacts
    gameplay_preference = None
    if v.job_id:
        job = db.get(Job, v.job_id)
        if job and isinstance(job.artifacts, dict):
            gameplay_preference = job.artifacts.get("gameplay_preference")

    if ki_id not in existing_ids:
        queue.append({
            "ki_id": ki_id,
            "gameplay_preference": gameplay_preference,
            "reuse_override": None,
        })
        config["idea_queue"] = queue
        auto.config = config
        flag_modified(auto, "config")

    db.commit()

    log.info(
        f"regenerate video #{video_id}: ki=#{ki_id}, clips_released={clips_released}, "
        f"ki_reverted={'yes' if ki_was_used else 'no'}, queued_at_end={'yes' if ki_id not in existing_ids else 'already_in_queue'}"
    )
    return {
        "success": True,
        "video_id": video_id,
        "knowledge_item_id": ki_id,
        "knowledge_item_title": ki.title,
        "clips_released": clips_released,
        "ki_reverted": ki_was_used,
        "queued_at_end": ki_id not in existing_ids,
        "gameplay_preference": gameplay_preference,
    }


# ── Voices (TTS reference audio) ──────────────────────────────────────────────


@router.get("/voices")
def list_voices(user: User = Depends(get_current_user)):
    """List available TTS voice reference files (uploaded via /voices/upload).

    REFACTORY_V2: voices are isolated per user under data/voices/{user_id}/.
    Falls back to the shared root for legacy voices (pre-refactory).
    """
    settings = get_settings()
    user_dir = settings.voices_dir / f"user_{user.id}"
    voices = []
    # User's own voices (isolated directory)
    if user_dir.exists():
        for p in sorted(user_dir.glob("*")):
            if p.suffix.lower() in (".wav", ".mp3", ".ogg", ".flac", ".m4a"):
                voices.append({
                    "filename": p.name,
                    "file_size": p.stat().st_size,
                    "file_size_kb": round(p.stat().st_size / 1024, 1),
                    "owner": "self",
                })
    # Legacy shared voices (root directory) — read-only for backward compat
    for p in sorted(settings.voices_dir.glob("*")):
        if p.is_file() and p.suffix.lower() in (".wav", ".mp3", ".ogg", ".flac", ".m4a"):
            voices.append({
                "filename": p.name,
                "file_size": p.stat().st_size,
                "file_size_kb": round(p.stat().st_size / 1024, 1),
                "owner": "shared",
            })
    return voices


@router.post("/voices/upload")
def upload_voice(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """Upload a TTS voice reference file (.wav or .mp3).

    The file is saved to data/voices/{user_id}/ and can be selected when
    creating a job via the 'voice' parameter. video-generate's XTTS uses
    this as the speaker_wav reference to clone the voice.

    REFACTORY_V2: voices are isolated per user under data/voices/{user_id}/.
    """
    settings = get_settings()
    if not file.filename:
        raise HTTPException(400, "filename is required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".wav", ".mp3", ".ogg", ".flac", ".m4a"):
        raise HTTPException(400, f"unsupported voice format '{suffix}' — use .wav or .mp3")
    user_dir = settings.voices_dir / f"user_{user.id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    dest = user_dir / file.filename
    if dest.exists():
        # Don't overwrite — add suffix
        dest = user_dir / f"{Path(file.filename).stem}_{int(time.time())}{suffix}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {
        "filename": dest.name,
        "file_size": dest.stat().st_size,
        "file_size_kb": round(dest.stat().st_size / 1024, 1),
        "path": str(dest),
    }


@router.delete("/voices/{filename}")
def delete_voice(filename: str, user: User = Depends(get_current_user)):
    """Delete an uploaded voice file.

    REFACTORY_V2: only deletes from the user's own directory. Legacy shared
    voices cannot be deleted by non-admin users.
    """
    settings = get_settings()
    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    user_dir = settings.voices_dir / f"user_{user.id}"
    p = user_dir / filename
    if not p.exists():
        raise HTTPException(404, "voice not found in your directory")
    p.unlink()
    return {"deleted": filename}
