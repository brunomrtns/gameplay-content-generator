"""Local DB sync — populates a temporary SQLite DB from VPS API data and
runs GenerationService locally.

The worker fetches all data needed for a generation job from the VPS API
(via GET /api/jobs/{id}/data), creates a temporary local SQLite database,
populates it with the fetched records, and then runs the existing
GenerationService against that local DB.

This allows the worker to reuse the entire generation pipeline (content
planning, script, TTS, gameplay selection, render, QA) without rewriting
it to use HTTP API calls. The pipeline runs exactly as it did when the
worker had direct DB access — just against a temporary local DB.

After generation completes, the results (ContentPlan, Script, Video, and
updated Job artifacts) are extracted from the local DB and sent back to
the VPS via POST /api/jobs/{id}/sync.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gpcg.domain.models import Base

log = logging.getLogger(__name__)


def _create_temp_db(db_path: Path) -> sessionmaker:
    """Create a temporary SQLite DB with all GPCG tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def populate_local_db(job_data: dict, db_path: Path) -> sessionmaker:
    """Create a local temp DB and populate it with data from the VPS API.

    Args:
        job_data: The payload from GET /api/jobs/{id}/data
        db_path: Where to create the SQLite file

    Returns:
        A sessionmaker bound to the populated temp DB.
    """
    from gpcg.domain.models import (
        User, Game, GameplaySource, GameplayEvent, Fact,
        ContentPlan, Script, Job, Automation,
    )

    SessionLocal = _create_temp_db(db_path)
    session = SessionLocal()

    try:
        # User (minimal — just needs to exist for FK constraints)
        job = job_data.get("job", {})
        user_id = job.get("user_id")
        if user_id:
            session.add(User(
                id=user_id,
                email="worker@local",
                is_active=True,
            ))
            session.flush()

        # Game
        game = job_data.get("game")
        if game:
            session.add(Game(
                id=game["id"],
                user_id=user_id,
                canonical_name=game["canonical_name"],
                aliases=game.get("aliases", []),
                platforms=game.get("platforms", []),
                capture_sources=game.get("capture_sources", []),
                camera_type=game.get("camera_type", "unknown"),
                metadata_json=game.get("metadata_json", {}),
            ))
            session.flush()

        # Facts
        for fact_data in job_data.get("facts", []):
            session.add(Fact(
                id=fact_data["id"],
                user_id=user_id,
                game_id=fact_data.get("game_id"),
                document_id=fact_data.get("document_id"),
                category=fact_data.get("category", "general"),
                claim=fact_data["claim"],
                source_ref=fact_data.get("source_ref"),
                verification=fact_data.get("verification", "unverified"),
                quality_score=fact_data.get("quality_score", 0.0),
                novelty_score=fact_data.get("novelty_score", 0.0),
                used_count=fact_data.get("used_count", 0),
                metadata_json=fact_data.get("metadata_json", {}),
            ))
        session.flush()

        # Gameplay sources + events
        for src_data in job_data.get("gameplay_sources", []):
            session.add(GameplaySource(
                id=src_data["id"],
                user_id=user_id,
                game_id=src_data.get("game_id"),
                file_path=src_data.get("file_path", ""),
                filename=src_data["filename"],
                file_hash=src_data.get("file_hash", ""),
                file_size=src_data.get("file_size", 0),
                duration=src_data.get("duration", 0.0),
                width=src_data.get("width", 0),
                height=src_data.get("height", 0),
                fps=src_data.get("fps", 0.0),
                codec=src_data.get("codec"),
                has_audio=src_data.get("has_audio", False),
                processing_status=src_data.get("processing_status", "ready"),
                metadata_json=src_data.get("metadata_json", {}),
            ))
            session.flush()

            # Events for this source
            for evt_data in src_data.get("events", []):
                session.add(GameplayEvent(
                    id=evt_data["id"],
                    source_id=src_data["id"],
                    start_time=evt_data["start_time"],
                    end_time=evt_data["end_time"],
                    event_type=evt_data["event_type"],
                    description=evt_data.get("description", ""),
                    characters=evt_data.get("characters", []),
                    location=evt_data.get("location"),
                    actions=evt_data.get("actions", []),
                    tags=evt_data.get("tags", []),
                    transcript=evt_data.get("transcript", ""),
                    visual_confidence=evt_data.get("visual_confidence", 0.0),
                    interesting_score=evt_data.get("interesting_score", 0.0),
                    analysis_version=evt_data.get("analysis_version", "v1"),
                    metadata_json=evt_data.get("metadata_json", {}),
                ))
        session.flush()

        # Content plan (if exists)
        plan_data = job_data.get("content_plan")
        if plan_data:
            session.add(ContentPlan(
                id=plan_data["id"],
                user_id=user_id,
                game_id=plan_data.get("game_id"),
                fact_id=plan_data.get("fact_id"),
                background_game_id=plan_data.get("background_game_id"),
                format=plan_data.get("format", "youtube_short"),
                target_duration=plan_data.get("target_duration", 60),
                topic=plan_data.get("topic", ""),
                hook=plan_data.get("hook", ""),
                tone=plan_data.get("tone", "curious"),
                energy=plan_data.get("energy", 0.7),
                music_mood=plan_data.get("music_mood", "neutral"),
                visual_strategy=plan_data.get("visual_strategy", "gameplay_compilation"),
                metadata_json=plan_data.get("metadata_json", {}),
            ))
            session.flush()

            # Scripts for this plan
            for script_data in job_data.get("scripts", []):
                session.add(Script(
                    id=script_data["id"],
                    content_plan_id=plan_data["id"],
                    draft=script_data.get("draft", ""),
                    optimized=script_data.get("optimized", ""),
                    final=script_data.get("final", ""),
                    status=script_data.get("status", "draft"),
                    char_count=script_data.get("char_count", 0),
                    originality_score=script_data.get("originality_score"),
                    originality_report=script_data.get("originality_report"),
                    rewrite_count=script_data.get("rewrite_count", 0),
                ))
        session.flush()

        # Job
        session.add(Job(
            id=job["id"],
            user_id=user_id,
            job_uuid=job["job_uuid"],
            type=job["type"],
            game_id=job.get("game_id"),
            content_plan_id=job.get("content_plan_id"),
            gameplay_source_id=job.get("gameplay_source_id"),
            status="running",
            stage=job.get("stage", "content_planning"),
            progress=0.0,
            attempts=job.get("attempts", 0),
            max_attempts=job.get("max_attempts", 3),
            artifacts=job.get("artifacts", {}),
            priority=job.get("priority", "normal"),
            required_capabilities=job.get("required_capabilities", []),
        ))
        session.flush()

        # Automation (for video customization settings)
        auto_data = job_data.get("automation")
        if auto_data:
            session.add(Automation(
                id=auto_data["id"],
                user_id=user_id,
                name=auto_data.get("name", ""),
                status=auto_data.get("status", "idle"),
                config=auto_data.get("config", {}),
                upload_config=auto_data.get("upload_config", {}),
            ))
        session.flush()

        session.commit()
        log.info(f"Local temp DB populated at {db_path}")
        return SessionLocal

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_generation_locally(
    job_data: dict,
    storage_root: Path,
    progress_callback: Optional[callable] = None,
) -> dict:
    """Run GenerationService against a local temp DB populated from VPS data.

    Args:
        job_data: Payload from GET /api/jobs/{id}/data
        storage_root: Local storage directory (ToshibaHD/gpcg)
        progress_callback: callable(stage: str, pct: float) for progress updates

    Returns:
        dict with keys:
            - status: "completed" | "failed"
            - error: error message (if failed)
            - video_path: local path to rendered video (if completed)
            - video: video metadata dict
            - content_plan: content plan dict (if created)
            - script: script dict (if created)
            - artifacts: updated job artifacts
    """
    job = job_data.get("job", {})
    job_id = job["id"]

    # Create temp DB
    temp_dir = storage_root / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    db_path = temp_dir / f"job_{job_id}.db"

    # Remove old temp DB if exists
    if db_path.exists():
        db_path.unlink()

    try:
        SessionLocal = populate_local_db(job_data, db_path)
    except Exception as e:
        log.error(f"Failed to populate local DB: {e}", exc_info=True)
        return {"status": "failed", "error": f"DB sync failed: {e}"}

    # Override the DB path AND data dir in settings so GenerationService:
    # 1. Uses our temp DB (not the VPS DB)
    # 2. Saves rendered videos to the HD (not the SSD)
    import os
    os.environ["GPCG_DB_PATH"] = str(db_path)
    os.environ["GPCG_DATA_DIR"] = str(storage_root / "data")

    # Clear the settings cache so the new paths take effect
    from gpcg.config import get_settings
    get_settings.cache_clear()

    # Also clear the DB engine cache
    from gpcg.infrastructure import database
    database._engine = None
    database._SessionLocal = None

    try:
        from gpcg.application.generation_service import GenerationService

        gen = GenerationService()

        if progress_callback:
            # Hook into the pipeline progress
            original_run = gen._run_pipeline

            def _run_with_progress(job_id_inner, *args, **kwargs):
                # The GenerationService updates job.stage/progress in the DB.
                # We poll the DB to report progress.
                return original_run(job_id_inner, *args, **kwargs)

        log.info(f"Running GenerationService for job #{job_id} on local DB")
        gen.run_job(job_id)

        # Extract results from the local DB
        session = SessionLocal()
        try:
            from gpcg.domain.models import Job as JobModel, ContentPlan, Script, Video

            job_row = session.query(JobModel).filter(JobModel.id == job_id).first()
            if not job_row:
                return {"status": "failed", "error": "Job not found in local DB after generation"}

            result: dict = {
                "status": job_row.status,
                "artifacts": job_row.artifacts or {},
            }

            if job_row.status == "failed":
                result["error"] = job_row.error or "Generation failed"
                return result

            # Extract content plan
            if job_row.content_plan_id:
                plan = session.query(ContentPlan).filter(ContentPlan.id == job_row.content_plan_id).first()
                if plan:
                    result["content_plan"] = {
                        "id": plan.id,
                        "game_id": plan.game_id,
                        "fact_id": plan.fact_id,
                        "background_game_id": plan.background_game_id,
                        "format": plan.format,
                        "target_duration": plan.target_duration,
                        "topic": plan.topic,
                        "hook": plan.hook,
                        "tone": plan.tone,
                        "energy": plan.energy,
                        "music_mood": plan.music_mood,
                        "visual_strategy": plan.visual_strategy,
                        "metadata_json": plan.metadata_json,
                    }

            # Extract script
            if job_row.content_plan_id:
                script = session.query(Script).filter(
                    Script.content_plan_id == job_row.content_plan_id
                ).order_by(Script.id.desc()).first()
                if script:
                    result["script"] = {
                        "id": script.id,
                        "content_plan_id": script.content_plan_id,
                        "draft": script.draft,
                        "optimized": script.optimized,
                        "final": script.final,
                        "status": script.status,
                        "char_count": script.char_count,
                        "originality_score": script.originality_score,
                        "originality_report": script.originality_report,
                        "rewrite_count": script.rewrite_count,
                    }

            # Extract video
            video = session.query(Video).filter(Video.job_id == job_id).first()
            if video:
                result["video"] = {
                    "id": video.id,
                    "file_path": video.file_path,
                    "duration": video.duration,
                    "width": video.width,
                    "height": video.height,
                    "qa_score": video.qa_score,
                    "qa_report": video.qa_report,
                    "status": video.status,
                }
                result["video_path"] = video.file_path

            return result

        finally:
            session.close()

    except Exception as e:
        log.error(f"Generation failed for job #{job_id}: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}
    finally:
        # Restore original env vars
        for var in ("GPCG_DB_PATH", "GPCG_DATA_DIR"):
            if var in os.environ:
                del os.environ[var]
        get_settings.cache_clear()
        database._engine = None
        database._SessionLocal = None
        # Clean up temp DB (always — it's a throwaway SQLite file)
        try:
            if db_path.exists():
                db_path.unlink()
        except OSError:
            pass
