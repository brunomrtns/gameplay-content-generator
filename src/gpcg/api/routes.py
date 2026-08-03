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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
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
    GameplayEvent,
    GameplaySource,
    Job,
    JobStatus,
    JobType,
    Script,
    User,
    Video,
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
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(GameplaySource).where(GameplaySource.user_id == user.id).order_by(GameplaySource.created_at.desc())
    if game_id is not None:
        stmt = stmt.where(GameplaySource.game_id == game_id)
    if status:
        stmt = stmt.where(GameplaySource.ingestion_status == status)
    sources = db.execute(stmt).scalars().all()
    return [
        {
            "id": s.id,
            "filename": s.filename,
            "game_id": s.game_id,
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
        }
        for s in sources
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
    game_id: int = Form(...),
    db: Session = Depends(get_db),
):
    """Manually assign a game to a source (for needs_review cases)."""
    s = db.get(GameplaySource, source_id)
    if s is None:
        raise HTTPException(404, "source not found")
    g = db.get(Game, game_id)
    if g is None:
        raise HTTPException(404, "game not found")
    s.game_id = g.id
    s.resolution_method = "manual"
    s.resolution_confidence = 1.0
    s.resolution_notes = "manually assigned"
    s.ingestion_status = "ready"
    db.commit()
    return {"ok": True}


@router.post("/inbox/scan")
def scan_inbox(user: User = Depends(get_current_user)):
    """Trigger a one-shot inbox scan."""
    svc = IngestionService()
    n = svc.scan_once()
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
            svc._ingest_file(file_path)
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
        # Fetch social_title from the job that produced this video
        social_title = None
        social_description = None
        social_tags = None
        if v.job_id:
            job = db.get(Job, v.job_id)
            if job and isinstance(job.artifacts, dict):
                social_title = job.artifacts.get("social_title")
                social_description = job.artifacts.get("social_description")
                social_tags = job.artifacts.get("social_tags")
        result.append(
            {
                "id": v.id,
                "game_id": v.game_id,
                "file_path": v.file_path,
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
            }
        )
    return result


@router.get("/videos/{video_id}/file")
def serve_video(video_id: int, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "video not found")
    p = Path(v.file_path)
    if not p.exists():
        raise HTTPException(404, "video file missing")
    return FileResponse(str(p), media_type="video/mp4")


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


@router.post("/videos/{video_id}/publish")
def publish_video(
    video_id: int,
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
    release_clips: bool = Query(False, description="Release gameplay clips used in this video"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a video and optionally release its gameplay clips.

    Args:
        video_id: ID of the video to delete.
        release_clips: If True, the gameplay segments used in this video
            are released back to the available pool (can be used in future videos).
            If False, the clips remain marked as used.

    The video file and thumbnail are also deleted from disk.
    """
    from gpcg.application.clip_usage_service import release_clip_usage

    v = db.get(Video, video_id)
    if v is None:
        raise HTTPException(404, "video not found")
    if v.user_id != user.id:
        raise HTTPException(403, "not your video")

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

    # Release clip usage if requested
    clips_released = 0
    if release_clips:
        clips_released = release_clip_usage(db, video_id)

    # Delete the video record
    db.delete(v)
    db.commit()

    log.info(f"deleted video #{video_id} (release_clips={release_clips}, clips_released={clips_released})")
    return {
        "success": True,
        "video_id": video_id,
        "clips_released": clips_released,
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
