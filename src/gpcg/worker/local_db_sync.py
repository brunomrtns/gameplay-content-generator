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
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gpcg.core.models import Base

log = logging.getLogger(__name__)


def _parse_dt(val):
    """Parse ISO string to datetime, or pass through if already datetime/None."""
    if val is None or isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _resolve_local_gameplay_path(vps_path: str, filename: str, storage_root: Path) -> Optional[str]:
    """Resolve a VPS gameplay file path to a local file path.

    The VPS stores gameplay at /app/data/gameplays/{filename} (container path).
    The VPS may add a hash prefix (e.g. "2ee37e60_") to the filename for
    deduplication. Locally, the original filename (without prefix) is used.

    Locally, gameplay files may be in:
    - {storage_root}/data/gameplays/{filename}
    - {storage_root}/data/inbox/{filename}
    - {storage_root}/gameplays/{source_id}_{filename}  (downloaded by remote worker)
    - Any dir listed in GPCG_GAMEPLAY_SEARCH_DIRS (colon-separated)
    - /media/bruno/ToshibaHD/Captures/{filename} (legacy Bruno PC)

    Returns the first matching path, or None if not found.
    """
    if not filename:
        return None

    # If the VPS path already exists locally, use it
    if vps_path and Path(vps_path).exists():
        return vps_path

    # The VPS may prepend a hash prefix like "2ee37e60_" to the filename.
    # Try both the original filename and the stripped version.
    import re
    stripped = re.sub(r'^[0-9a-f]{8}_', '', filename)
    candidates = [filename]
    if stripped != filename:
        candidates.append(stripped)

    # Search common locations (storage_root-relative)
    search_dirs = [
        storage_root / "data" / "gameplays",
        storage_root / "data" / "inbox",
        storage_root / "gameplays",  # downloaded by remote worker
    ]

    # Extra search dirs from env var (colon-separated, like PATH)
    extra = os.environ.get("GPCG_GAMEPLAY_SEARCH_DIRS", "")
    if extra:
        for d in extra.split(":"):
            d = d.strip()
            if d:
                search_dirs.append(Path(d))

    # Legacy Bruno PC paths (only if they exist on this machine)
    legacy_dirs = [
        Path("/media/bruno/ToshibaHD/Captures"),
        Path("/media/bruno/ToshibaHD/gpcg/data/gameplays"),
        Path("/media/bruno/ToshibaHD/gpcg/data/inbox"),
        Path("/media/bruno/ToshibaHD"),
    ]
    for d in legacy_dirs:
        if d.exists():
            search_dirs.append(d)

    for d in search_dirs:
        for name in candidates:
            candidate = d / name
            if candidate.exists():
                return str(candidate)

    # Last resort: find by filename under the first existing legacy dir
    # or storage_root (only if it's a real directory tree)
    import subprocess
    find_roots = []
    for d in legacy_dirs:
        if d.exists():
            find_roots.append(str(d))
            break
    if not find_roots:
        find_roots.append(str(storage_root))
    for name in candidates:
        for root in find_roots:
            try:
                result = subprocess.run(
                    ["find", root, "-name", name, "-type", "f"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip().split("\n")[0]
            except Exception:
                pass

    return None


def _create_temp_db(db_path: Path) -> sessionmaker:
    """Create a temporary SQLite DB with all GPCG tables."""
    # Import ALL models so Base.metadata knows about every table
    import gpcg.core.models  # noqa: F401 — side effect: registers core tables
    import gpcg.domains.games.models  # noqa: F401 — side effect: registers games tables

    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    tables_before = list(Base.metadata.tables.keys())
    log.info(f"_create_temp_db: {len(tables_before)} tables registered: {tables_before}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def populate_local_db(job_data: dict, db_path: Path, storage_root: Path = None) -> sessionmaker:
    """Create a local temp DB and populate it with data from the VPS API.

    Args:
        job_data: The payload from GET /api/jobs/{id}/data
        db_path: Where to create the SQLite file

    Returns:
        A sessionmaker bound to the populated temp DB.
    """
    from gpcg.core.models import (
    User,
    Fact,
    ContentPlan,
    Script,
    Job,
    Automation,
    KnowledgeItem,
    ChannelProfile,
)
    from gpcg.domains.games.models import (
    Game,
    GameplaySource,
    GameplayEvent,
    GameplayAsset,
    GameplayClipUsage,
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

        # Background game (for curiosity_short — gameplay runs in background)
        bg_game = job_data.get("background_game")
        if bg_game:
            # Only add if not already added (same game as main game)
            existing = session.get(Game, bg_game["id"])
            if existing is None:
                session.add(Game(
                    id=bg_game["id"],
                    user_id=user_id,
                    canonical_name=bg_game["canonical_name"],
                    aliases=bg_game.get("aliases", []),
                    platforms=bg_game.get("platforms", []),
                    capture_sources=bg_game.get("capture_sources", []),
                    camera_type=bg_game.get("camera_type", "unknown"),
                    metadata_json=bg_game.get("metadata_json", {}),
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
            # Resolve local file path: VPS sends container path (/app/data/...),
            # we need to find the file on the local HD
            vps_path = src_data.get("file_path", "")
            filename = src_data.get("filename", "")
            local_path = _resolve_local_gameplay_path(vps_path, filename, storage_root or Path("/media/bruno/ToshibaHD/gpcg"))
            if local_path:
                log.info(f"Resolved gameplay path: {vps_path} → {local_path}")
            else:
                log.warning(f"Could not resolve local path for {filename} (VPS: {vps_path})")
            # Normalize metadata_json to match model expectations:
            # model reads analysis status from metadata_json["analysis"]["status"]
            # but VPS may store it as metadata_json["analysis_status"]
            raw_meta = src_data.get("metadata_json", {})
            if isinstance(raw_meta, str):
                import json as _json
                try:
                    raw_meta = _json.loads(raw_meta)
                except (ValueError, TypeError):
                    raw_meta = {}
            if not isinstance(raw_meta, dict):
                raw_meta = {}

            # Ensure analysis info is in the expected nested format
            if "analysis_status" in raw_meta and "analysis" not in raw_meta:
                raw_meta["analysis"] = {
                    "status": raw_meta.pop("analysis_status"),
                    "version": raw_meta.get("analysis_version", "v1"),
                    "event_count": raw_meta.get("events_count", 0),
                }
            elif "analysis" not in raw_meta:
                # Default to ready if we have events
                events_list = src_data.get("events", [])
                raw_meta["analysis"] = {
                    "status": "ready" if events_list else "pending",
                    "version": "v1",
                    "event_count": len(events_list),
                }

            session.add(GameplaySource(
                id=src_data["id"],
                user_id=user_id,
                game_id=src_data.get("game_id"),
                file_path=local_path or src_data.get("file_path", ""),
                filename=src_data["filename"],
                file_hash=src_data.get("file_hash", ""),
                file_size=src_data.get("file_size", 0),
                duration=src_data.get("duration", 0.0),
                width=src_data.get("width", 0),
                height=src_data.get("height", 0),
                fps=src_data.get("fps", 0.0),
                codec=src_data.get("codec"),
                has_audio=src_data.get("has_audio", False),
                ingestion_status="ready",
                processing_status=src_data.get("processing_status", "ready"),
                metadata_json=raw_meta,
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
            # Assets for this source (clips used by GameplaySelector)
            for asset_data in src_data.get("assets", []):
                session.add(GameplayAsset(
                    id=asset_data["id"],
                    source_id=src_data["id"],
                    label=asset_data.get("label", ""),
                    start_sec=asset_data["start_sec"],
                    end_sec=asset_data["end_sec"],
                    duration=asset_data["duration"],
                    used_count=asset_data.get("used_count", 0),
                    metadata_json=asset_data.get("metadata_json", {}),
                ))
            # V2: Clip usage records (so GameplaySelector avoids used segments)
            # REFACTORY_V2: include consumer_user_id for per-consumer filtering
            for cu_data in src_data.get("clip_usages", []):
                session.add(GameplayClipUsage(
                    id=cu_data["id"],
                    video_id=cu_data.get("video_id"),
                    source_id=src_data["id"],
                    consumer_user_id=cu_data.get("consumer_user_id"),
                    start_sec=cu_data["start_sec"],
                    end_sec=cu_data["end_sec"],
                    duration=cu_data.get("duration", cu_data["end_sec"] - cu_data["start_sec"]),
                ))
        session.flush()

        # Record the initial clip usage count so we can identify NEW records
        # created during this job (for sync back to VPS without duplicates)
        from gpcg.domains.games.models import GameplayClipUsage as _ClipUsage
        initial_clip_usage_count = session.query(_ClipUsage).count()
        job_data["_initial_clip_usage_count"] = initial_clip_usage_count

        # Knowledge items (V2 content intelligence — used by ContentPlanningService)
        ki_list_raw = job_data.get("knowledge_items", [])
        log.info(f"populate_local_db: inserting {len(ki_list_raw)} knowledge_items")
        for ki_data in ki_list_raw:
            session.add(KnowledgeItem(
                id=ki_data["id"],
                user_id=ki_data.get("user_id", user_id),
                game_id=ki_data.get("game_id"),
                is_public=ki_data.get("is_public", True),
                source_type=ki_data.get("source_type", "rss"),
                title=ki_data.get("title", ""),
                content=ki_data.get("content", ""),
                item_type=ki_data.get("item_type", "news"),
                editorial_score=ki_data.get("editorial_score", 0.0),
                status=ki_data.get("status", "fresh"),
                franchise=ki_data.get("franchise"),
                developer=ki_data.get("developer"),
                source_url=ki_data.get("source_url"),
                source_name=ki_data.get("source_name"),
                published_at=_parse_dt(ki_data.get("published_at")),
                collected_at=_parse_dt(ki_data.get("collected_at")),
                tags=ki_data.get("tags"),
                content_hash=ki_data.get("content_hash"),
            ))
        session.flush()
        log.info(f"populate_local_db: flushed {len(ki_list_raw)} knowledge_items")

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

        # Job — reset status to "queued" so GenerationService.run_job() will run
        # Parse artifacts: VPS sends JSON string, local DB needs dict
        raw_artifacts = job.get("artifacts", {})
        if isinstance(raw_artifacts, str):
            import json as _json
            try:
                raw_artifacts = _json.loads(raw_artifacts)
            except (ValueError, TypeError):
                raw_artifacts = {}

        session.add(Job(
            id=job["id"],
            user_id=user_id,
            job_uuid=job["job_uuid"],
            type=job["type"],
            domain=job.get("domain", "games"),
            game_id=job.get("game_id"),
            content_plan_id=job.get("content_plan_id"),
            gameplay_source_id=job.get("gameplay_source_id"),
            status="queued",
            stage="queued",
            progress=0.0,
            attempts=job.get("attempts", 0),
            max_attempts=job.get("max_attempts", 3),
            artifacts=raw_artifacts,
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

        # V3: ChannelProfile — sync from VPS so the pipeline can use it.
        # GenerationService._run_pipeline loads ChannelProfile from the local
        # DB (session.query(ChannelProfile).filter(user_id==...).first()).
        # Without this record, channel context is always None in the worker.
        profile_data = job_data.get("channel_profile")
        if profile_data:
            session.add(ChannelProfile(
                id=profile_data["id"],
                user_id=profile_data.get("user_id", user_id),
                channel_description=profile_data.get("channel_description", ""),
                niche=profile_data.get("niche", ""),
                target_audience=profile_data.get("target_audience", ""),
                tone_of_voice=profile_data.get("tone_of_voice", ""),
                narrative_style=profile_data.get("narrative_style", ""),
                content_goals=profile_data.get("content_goals", ""),
                special_rules=profile_data.get("special_rules", ""),
                metadata_json=profile_data.get("metadata_json", {}),
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
        SessionLocal = populate_local_db(job_data, db_path, storage_root=storage_root)
    except Exception as e:
        log.error(f"Failed to populate local DB: {e}", exc_info=True)
        return {"status": "failed", "error": f"DB sync failed: {e}"}

    # Create a session scope bound to the temp DB (no global DB state mutation).
    # This replaces the old approach of mutating os.environ["GPCG_DB_PATH"] +
    # database._engine = None. The session scope is passed explicitly to
    # GenerationService, so the global engine is never touched.
    from gpcg.infrastructure.database import make_session_scope

    temp_session_scope = make_session_scope(SessionLocal)

    # GPCG_DATA_DIR controls where rendered videos are saved (file storage,
    # not database). Set it so GenerationService saves to the worker's HD,
    # not the default ./data/. This is a file-storage path, not a DB path.
    os.environ["GPCG_DATA_DIR"] = str(storage_root / "data")

    # get_settings() is @lru_cache(maxsize=1). RemoteWorker.__init__() calls
    # it during startup, so the cache is already populated with the default
    # gpcg_data_dir. We must clear the cache so GenerationService picks up
    # the new GPCG_DATA_DIR we just set.
    from gpcg.config import get_settings
    get_settings.cache_clear()

    try:
        from gpcg.application.generation_service import GenerationService

        gen = GenerationService(session_scope=temp_session_scope)

        if progress_callback:
            # Hook into the pipeline progress
            original_run = gen._run_pipeline

            def _run_with_progress(job_id_inner, *args, **kwargs):
                # The GenerationService updates job.stage/progress in the DB.
                # We poll the DB to report progress.
                return original_run(job_id_inner, *args, **kwargs)

        log.info(f"Running GenerationService for job #{job_id} on local DB")
        gen.run_job(job_id)

        # Extract results from the local DB using the same temp session scope.
        # Since GenerationService used temp_session_scope (bound to SessionLocal),
        # we use the same scope for extraction — same engine, no visibility issues.
        with temp_session_scope() as session:
            from gpcg.core.models import ContentPlan, Script, Video
            from gpcg.core.models import Job as JobModel

            job_row = session.query(JobModel).filter(JobModel.id == job_id).first()
            if not job_row:
                return {"status": "failed", "error": "Job not found in local DB after generation"}

            result: dict = {
                "status": job_row.status,
                "artifacts": dict(job_row.artifacts or {}),
            }
            log.info(f"Local DB extract: job #{job_id} status={job_row.status} artifacts_keys={list((job_row.artifacts or {}).keys())}")

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
                    "content_plan_id": video.content_plan_id,
                    "knowledge_item_id": video.knowledge_item_id,
                    "file_path": video.file_path,
                    "duration": video.duration,
                    "width": video.width,
                    "height": video.height,
                    "qa_score": video.qa_score,
                    "qa_report": video.qa_report,
                    "status": video.status,
                }
                result["video_path"] = video.file_path

            # V2: Extract ONLY NEW clip usage records created during this job.
            # The local DB was populated from the VPS at the start, so we must
            # NOT re-sync pre-existing records (that would create duplicates).
            # We track the count at population time and only send the new ones.
            from gpcg.domains.games.models import GameplayClipUsage as ClipUsage
            initial_clip_usage_count = job_data.get("_initial_clip_usage_count", 0)
            all_clip_usages = session.query(ClipUsage).order_by(ClipUsage.id).all()
            new_clip_usages = all_clip_usages[initial_clip_usage_count:]
            if new_clip_usages:
                result["clip_usages"] = [{
                    "source_id": cu.source_id,
                    "start_sec": cu.start_sec,
                    "end_sec": cu.end_sec,
                    "duration": cu.duration,
                    "consumer_user_id": cu.consumer_user_id,
                } for cu in new_clip_usages]

            return result

    except Exception as e:
        log.error(f"Generation failed for job #{job_id}: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}
    finally:
        # Clean up file-storage env var (GPCG_DATA_DIR). The global DB engine
        # was never touched — no database._engine or GPCG_DB_PATH cleanup needed.
        os.environ.pop("GPCG_DATA_DIR", None)
        # Clear settings cache so subsequent calls re-read env vars (which
        # no longer have GPCG_DATA_DIR set, reverting to the default).
        get_settings.cache_clear()
        # Clean up temp DB (always — it's a throwaway SQLite file)
        try:
            if db_path.exists():
                db_path.unlink()
        except OSError:
            pass
