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

router = APIRouter(tags=["gpcg"])


# ── Games ─────────────────────────────────────────────────────────────────────


@router.get("/games")
def list_games(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    games = db.query(Game).filter(Game.user_id == user.id).order_by(Game.canonical_name).all()
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
                "aliases": g.aliases,
                "platforms": g.platforms,
                "capture_sources": g.capture_sources,
                "counts": {"sources": sources, "assets": assets, "facts": facts, "videos": videos},
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

    # Read and hash
    content = file.file.read()
    file_hash = hashlib.sha256(content).hexdigest()

    # Check for duplicates
    existing = db.query(GameplaySource).filter(
        GameplaySource.user_id == user.id,
        GameplaySource.file_hash == file_hash,
    ).first()
    if existing:
        raise HTTPException(409, "Este arquivo já foi enviado")

    # Save file to temp storage
    filename = file.filename or "upload.mp4"
    safe_name = f"{file_hash[:8]}_{filename}"
    file_path = upload_dir / safe_name
    file_path.write_bytes(content)

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
            file_size=len(content),
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
):
    """List documents. Either for a specific game, or general (general=true)."""
    if general:
        stmt = select(Document).where(Document.game_id.is_(None))
    elif game_id is not None:
        stmt = select(Document).where(Document.game_id == game_id)
    else:
        stmt = select(Document)
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
):
    """List facts. Either for a specific game, or general (general=true)."""
    if general:
        stmt = select(Fact).where(Fact.game_id.is_(None))
    elif game_id is not None:
        stmt = select(Fact).where(Fact.game_id == game_id)
    else:
        stmt = select(Fact)
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
def list_plans(game_id: Optional[int] = Query(None), db: Session = Depends(get_db)):
    stmt = select(ContentPlan).order_by(ContentPlan.created_at.desc())
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
    db: Session = Depends(get_db),
):
    """Create a generation job for a game.

    Optional customization params (override config defaults):
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
    voice_path = ""
    if voice:
        vp = settings.voices_dir / voice
        if not vp.exists():
            raise HTTPException(404, f"voice '{voice}' not found — upload it first")
        voice_path = str(vp)
    svc = GenerationService()
    job = svc.create_job(
        g.canonical_name,
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
    db: Session = Depends(get_db),
):
    """Create a curiosity_short job: general curiosity fact + gameplay background.

    The fact comes from the general pool (game_id=NULL).
    background_game_id is the game whose gameplay runs in the background.
    If fact_id is omitted, the system auto-picks the best general fact.

    Optional customization params same as /jobs/generate.
    """
    g = db.get(Game, background_game_id)
    if g is None:
        raise HTTPException(404, "background game not found")
    settings = get_settings()
    voice_path = ""
    if voice:
        vp = settings.voices_dir / voice
        if not vp.exists():
            raise HTTPException(404, f"voice '{voice}' not found — upload it first")
        voice_path = str(vp)
    svc = GenerationService()
    job = svc.create_curiosity_job(
        background_game_id,
        fact_id=fact_id,
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
    if v is None or not v.thumbnail_path:
        raise HTTPException(404, "thumbnail not found")
    p = Path(v.thumbnail_path)
    if not p.exists():
        raise HTTPException(404, "thumbnail file missing")
    return FileResponse(str(p), media_type="image/jpeg")


# ── Voices (TTS reference audio) ──────────────────────────────────────────────


@router.get("/voices")
def list_voices():
    """List available TTS voice reference files (uploaded via /voices/upload)."""
    settings = get_settings()
    voices_dir = settings.voices_dir
    voices = []
    for p in sorted(voices_dir.glob("*")):
        if p.suffix.lower() in (".wav", ".mp3", ".ogg", ".flac", ".m4a"):
            voices.append({
                "filename": p.name,
                "file_size": p.stat().st_size,
                "file_size_kb": round(p.stat().st_size / 1024, 1),
            })
    return voices


@router.post("/voices/upload")
def upload_voice(file: UploadFile = File(...)):
    """Upload a TTS voice reference file (.wav or .mp3).

    The file is saved to data/voices/ and can be selected when creating a job
    via the 'voice' parameter. video-generate's XTTS uses this as the
    speaker_wav reference to clone the voice.
    """
    settings = get_settings()
    if not file.filename:
        raise HTTPException(400, "filename is required")
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".wav", ".mp3", ".ogg", ".flac", ".m4a"):
        raise HTTPException(400, f"unsupported voice format '{suffix}' — use .wav or .mp3")
    dest = settings.voices_dir / file.filename
    if dest.exists():
        # Don't overwrite — add suffix
        dest = settings.voices_dir / f"{Path(file.filename).stem}_{int(time.time())}{suffix}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {
        "filename": dest.name,
        "file_size": dest.stat().st_size,
        "file_size_kb": round(dest.stat().st_size / 1024, 1),
        "path": str(dest),
    }


@router.delete("/voices/{filename}")
def delete_voice(filename: str):
    """Delete an uploaded voice file."""
    settings = get_settings()
    # Prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    p = settings.voices_dir / filename
    if not p.exists():
        raise HTTPException(404, "voice not found")
    p.unlink()
    return {"deleted": filename}
