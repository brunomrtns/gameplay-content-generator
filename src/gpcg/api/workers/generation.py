"""Generation endpoints — job data fetch and result synchronization.

The worker calls ``GET /jobs/{job_id}/data`` to fetch everything it needs to
run GenerationService locally (game, facts, gameplay sources/events, content
plans, scripts, automation config, channel profile, kids domain data). After
running the pipeline, it calls ``POST /jobs/{job_id}/sync`` to persist the
ContentPlan/Script/Video records it created back to the VPS.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from gpcg.core.models import Job, JobStatus
from gpcg.domains.games.models import (
    GameplayClipUsage,
    GameplayEvent,
    GameplaySource,
)
from gpcg.infrastructure.database import get_db

from gpcg.api.workers._common import (
    _ensure_dict,
    worker_auth,
)
from gpcg.api.workers.jobs import _serialize_job

log = logging.getLogger(__name__)
router = APIRouter(tags=["workers"])


# ── Generation job data (worker fetches all data needed for generation) ──────


@router.get("/jobs/{job_id}/data")
def get_job_data(
    job_id: int,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Fetch all data the worker needs to run a generation job.

    Returns the job + all related records (game, facts, gameplay sources,
    gameplay events, content plans, scripts, automation config) in a single
    payload. The worker uses this to populate a local temp DB and run
    GenerationService locally.
    """
    from gpcg.core.models import (
    Fact,
    ContentPlan,
    Script,
    Automation,
)
    from gpcg.domains.games.models import Game, GameplayAsset

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Domain guard: reject data fetch for cancelled or old-domain jobs
    if job.status == JobStatus.cancelled.value:
        raise HTTPException(status_code=409, detail="Job has been cancelled.")
    if job.user_id:
        from gpcg.core.models import ChannelProfile, ContentDomain
        profile = db.query(ChannelProfile).filter(
            ChannelProfile.user_id == job.user_id
        ).first()
        current_domain = profile.domain if profile else ContentDomain.games.value
        if job.domain and job.domain != current_domain:
            raise HTTPException(status_code=409, detail="Job belongs to a previous domain.")

    data: dict = {"job": _serialize_job(job)}

    # Game
    if job.game_id:
        game = db.query(Game).filter(Game.id == job.game_id).first()
        if game:
            data["game"] = {
                "id": game.id, "canonical_name": game.canonical_name,
                "aliases": game.aliases, "camera_type": game.camera_type,
                "platforms": game.platforms, "capture_sources": game.capture_sources,
                "metadata_json": game.metadata_json,
            }

    # Background game (for curiosity_short — the game whose gameplay runs in background)
    bg_game_id = job.artifacts.get("background_game_id") if job.artifacts else None
    if bg_game_id and bg_game_id != job.game_id:
        bg_game = db.query(Game).filter(Game.id == bg_game_id).first()
        if bg_game:
            data["background_game"] = {
                "id": bg_game.id, "canonical_name": bg_game.canonical_name,
                "aliases": bg_game.aliases, "camera_type": bg_game.camera_type,
                "platforms": bg_game.platforms, "capture_sources": bg_game.capture_sources,
                "metadata_json": bg_game.metadata_json,
            }

    # Content plan (if exists)
    if job.content_plan_id:
        plan = db.query(ContentPlan).filter(ContentPlan.id == job.content_plan_id).first()
        if plan:
            data["content_plan"] = {
                "id": plan.id, "game_id": plan.game_id,
                "fact_id": plan.fact_id, "background_game_id": plan.background_game_id,
                "format": plan.format, "target_duration": plan.target_duration,
                "topic": plan.topic, "hook": plan.hook, "tone": plan.tone,
                "energy": plan.energy, "music_mood": plan.music_mood,
                "visual_strategy": plan.visual_strategy,
                "metadata_json": plan.metadata_json,
            }
            # Scripts for this plan
            scripts = db.query(Script).filter(Script.content_plan_id == plan.id).all()
            data["scripts"] = [{
                "id": s.id, "content_plan_id": s.content_plan_id,
                "draft": s.draft, "optimized": s.optimized, "final": s.final,
                "status": s.status, "char_count": s.char_count,
                "originality_score": s.originality_score,
                "originality_report": s.originality_report,
                "rewrite_count": s.rewrite_count,
            } for s in scripts]

    # Facts for the game — REFACTORY_V2: filter by visibility (own + shared + public)
    if job.game_id:
        from gpcg.domain.visibility import visible_to_user
        fact_vis = visible_to_user(Fact.user_id, Fact.is_public, job.user_id)
        facts = db.query(Fact).filter(
            Fact.game_id == job.game_id, fact_vis
        ).all()
        data["facts"] = [{
            "id": f.id, "game_id": f.game_id, "document_id": f.document_id,
            "category": f.category, "claim": f.claim,
            "source_ref": f.source_ref, "verification": f.verification,
            "quality_score": f.quality_score, "novelty_score": f.novelty_score,
            "used_count": f.used_count, "metadata_json": f.metadata_json,
        } for f in facts]

    # Knowledge items for the game (V2 content intelligence)
    # REFACTORY_V2: filter by visibility (own + shared + public)
    # V2: Sync game-specific KIs first, then general KIs for curiosity_short
    try:
        from gpcg.core.models import KnowledgeItem, KnowledgeItemStatus
        from gpcg.domain.visibility import visible_to_user as _ki_vis
        ki_vis = _ki_vis(KnowledgeItem.user_id, KnowledgeItem.is_public, job.user_id)

        # Game-specific KIs (always include all for the job's game)
        game_kis = []
        if job.game_id:
            game_kis = db.query(KnowledgeItem).filter(
                KnowledgeItem.status == KnowledgeItemStatus.fresh.value,
                KnowledgeItem.game_id == job.game_id,
                ki_vis,
            ).order_by(KnowledgeItem.editorial_score.desc()).limit(20).all()

        # General KIs (game_id=None) for curiosity_short fallback
        general_kis = db.query(KnowledgeItem).filter(
            KnowledgeItem.status == KnowledgeItemStatus.fresh.value,
            KnowledgeItem.game_id.is_(None),
            ki_vis,
        ).order_by(KnowledgeItem.editorial_score.desc()).limit(30).all()

        ki_list = game_kis + general_kis

        # V3: Ensure the queued KnowledgeItem is always included in the
        # sync data, even if it doesn't match the game/general filters
        # (e.g. a news KI about Xbox used with Crash Team Racing background).
        queued_ki_id = (_ensure_dict(job.artifacts) or {}).get("queued_knowledge_item_id")
        if queued_ki_id:
            existing_ids = {ki.id for ki in ki_list}
            queued_ki = db.get(KnowledgeItem, queued_ki_id)
            if queued_ki and queued_ki.id not in existing_ids:
                ki_list = [queued_ki] + ki_list
        data["knowledge_items"] = [{
            "id": ki.id, "user_id": ki.user_id, "game_id": ki.game_id,
            "is_public": ki.is_public,
            "source_type": ki.source_type, "title": ki.title,
            "content": ki.content,
            "item_type": ki.item_type,
            "editorial_score": ki.editorial_score,
            "status": ki.status,
            "franchise": ki.franchise,
            "developer": ki.developer,
            "source_url": ki.source_url,
            "source_name": ki.source_name,
            "published_at": ki.published_at.isoformat() if ki.published_at else None,
            "collected_at": ki.collected_at.isoformat() if ki.collected_at else None,
            "tags": ki.tags,
            "content_hash": ki.content_hash,
        } for ki in ki_list]
    except Exception as e:
        log.warning(f"Failed to sync knowledge items: {e}")
        data["knowledge_items"] = []

    # Gameplay sources + events — only for Games jobs.
    # Kids jobs don't use gameplay sources; sending them wastes bandwidth
    # and could confuse the worker's local DB sync.
    if job.domain != "kids":
        from gpcg.core.models import Automation
        from gpcg.domain.visibility import user_allows_public_gameplays
        from sqlalchemy import or_

        _automation = db.query(Automation).filter(
            Automation.user_id == job.user_id
        ).first()
        _allows_public = user_allows_public_gameplays(
            _automation.config if _automation else None
        )

        if _allows_public:
            sources_query = db.query(GameplaySource).filter(
                or_(
                    GameplaySource.user_id == job.user_id,
                    GameplaySource.is_public == True,
                ),
                GameplaySource.processing_status == "ready",
                GameplaySource.enabled == True,
            )
        else:
            sources_query = db.query(GameplaySource).filter(
                GameplaySource.user_id == job.user_id,
                GameplaySource.processing_status == "ready",
                GameplaySource.enabled == True,
            )
        sources = sources_query.all()
    else:
        sources = []
    data["gameplay_sources"] = []
    for src in sources:
        src_data = {
            "id": src.id, "game_id": src.game_id,
            "filename": src.filename, "file_hash": src.file_hash,
            "file_size": src.file_size, "duration": src.duration,
            "width": src.width, "height": src.height, "fps": src.fps,
            "codec": src.codec, "has_audio": src.has_audio,
            "processing_status": src.processing_status,
            "metadata_json": src.metadata_json,
            "file_path": src.file_path,  # worker resolves to local path
            "enabled": src.enabled,
        }
        # Events for this source
        events = db.query(GameplayEvent).filter(GameplayEvent.source_id == src.id).all()
        src_data["events"] = [{
            "id": e.id, "source_id": e.source_id,
            "start_time": e.start_time, "end_time": e.end_time,
            "event_type": e.event_type, "description": e.description,
            "characters": e.characters, "location": e.location,
            "actions": e.actions, "tags": e.tags,
            "transcript": e.transcript,
            "visual_confidence": e.visual_confidence,
            "interesting_score": e.interesting_score,
            "analysis_version": e.analysis_version,
            "metadata_json": e.metadata_json,
        } for e in events]
        # Assets for this source (clips that the GameplaySelector uses)
        assets = db.query(GameplayAsset).filter(GameplayAsset.source_id == src.id).all()
        src_data["assets"] = [{
            "id": a.id, "source_id": a.source_id,
            "label": a.label, "start_sec": a.start_sec,
            "end_sec": a.end_sec, "duration": a.duration,
            "used_count": a.used_count,
            "metadata_json": a.metadata_json,
        } for a in assets]
        # V2: Clip usage records (so worker can avoid reusing segments)
        # REFACTORY_V2: include consumer_user_id for per-consumer filtering
        clip_usages = db.query(GameplayClipUsage).filter(GameplayClipUsage.source_id == src.id).all()
        src_data["clip_usages"] = [{
            "id": cu.id, "video_id": cu.video_id, "source_id": cu.source_id,
            "consumer_user_id": cu.consumer_user_id,
            "start_sec": cu.start_sec, "end_sec": cu.end_sec,
            "duration": cu.duration,
        } for cu in clip_usages]
        data["gameplay_sources"].append(src_data)

    # Kids domain data — only for Kids jobs (domain == "kids")
    if job.domain == "kids":
        from gpcg.domains.kids.models import (
            KidsTopic, StoryAsset, AssetProcessingStatus,
            KidsMediaEvent, AssetClipUsage,
        )
        topic_id = (_ensure_dict(job.artifacts) or {}).get("topic_id")
        if topic_id:
            topic = db.query(KidsTopic).filter(KidsTopic.id == topic_id).first()
            if topic:
                data["kids_topic"] = {
                    "id": topic.id, "user_id": topic.user_id,
                    "title": topic.title, "slug": topic.slug,
                    "category": topic.category, "age_range": topic.age_range,
                    "description": topic.description,
                    "metadata_json": topic.metadata_json,
                }
        # Story assets from the channel library (not just topic-linked).
        # KidsMediaRetriever selects from the full library using events +
        # tags + description for semantic matching. Include all ready
        # assets owned by the user, plus public assets from other users.
        assets = db.query(StoryAsset).filter(
            StoryAsset.user_id == job.user_id,
            StoryAsset.processing_status == AssetProcessingStatus.ready.value,
        ).all()
        # Also include public assets from other users (for fallback)
        public_assets = db.query(StoryAsset).filter(
            StoryAsset.is_public == True,
            StoryAsset.user_id != job.user_id,
            StoryAsset.processing_status == AssetProcessingStatus.ready.value,
        ).all() if job.user_id else []
        all_assets = assets + public_assets
        asset_ids = [a.id for a in all_assets]
        data["story_assets"] = [{
            "id": a.id, "user_id": a.user_id, "topic_id": a.topic_id,
            "filename": a.filename, "storage_key": a.storage_key,
            "file_hash": a.file_hash, "file_size": a.file_size,
            "media_kind": a.media_kind,
            "width": a.width, "height": a.height,
            "duration": a.duration, "codec": a.codec,
            "has_audio": a.has_audio, "thumbnail_key": a.thumbnail_key,
            "processing_status": a.processing_status,
            "tags": a.tags or [],
            "description": a.description or "",
            "is_public": a.is_public,
            "metadata_json": a.metadata_json,
        } for a in all_assets]

        # Kids media events (semantic index — same as GameplayEvent for Games)
        if asset_ids:
            events = db.query(KidsMediaEvent).filter(
                KidsMediaEvent.asset_id.in_(asset_ids)
            ).order_by(KidsMediaEvent.asset_id, KidsMediaEvent.start_time).all()
            data["kids_media_events"] = [{
                "id": e.id, "asset_id": e.asset_id,
                "start_time": e.start_time, "end_time": e.end_time,
                "event_type": e.event_type, "description": e.description,
                "characters": e.characters, "location": e.location,
                "actions": e.actions, "tags": e.tags,
                "transcript": e.transcript,
                "visual_confidence": e.visual_confidence,
                "interesting_score": e.interesting_score,
                "analysis_version": e.analysis_version,
            } for e in events]

            # Asset clip usage records (so worker can avoid reusing segments)
            clip_usages = db.query(AssetClipUsage).filter(
                AssetClipUsage.asset_id.in_(asset_ids)
            ).all()
            data["kids_clip_usages"] = [{
                "id": cu.id, "video_id": cu.video_id,
                "asset_id": cu.asset_id,
                "consumer_user_id": cu.consumer_user_id,
                "start_sec": cu.start_sec, "end_sec": cu.end_sec,
                "duration": cu.duration,
            } for cu in clip_usages]

    # Automation config (for video customization settings)
    automation = db.query(Automation).filter(Automation.user_id == job.user_id).first()
    if automation:
        data["automation"] = {
            "id": automation.id, "user_id": automation.user_id,
            "name": automation.name, "status": automation.status,
            "config": automation.config, "upload_config": automation.upload_config,
        }

    # V3: ChannelProfile — sync to worker so the pipeline can use channel
    # context in content_planning, story_finding, editorial_planning, script.
    # Without this, GenerationService._run_pipeline loads ChannelProfile from
    # the local DB and always gets None (G1 gap).
    from gpcg.core.models import ChannelProfile as _ChannelProfile
    profile = db.query(_ChannelProfile).filter(
        _ChannelProfile.user_id == job.user_id
    ).first()
    if profile:
        data["channel_profile"] = {
            "id": profile.id,
            "user_id": profile.user_id,
            "channel_description": profile.channel_description,
            "niche": profile.niche,
            "target_audience": profile.target_audience,
            "tone_of_voice": profile.tone_of_voice,
            "narrative_style": profile.narrative_style,
            "content_goals": profile.content_goals,
            "special_rules": profile.special_rules,
            "metadata_json": profile.metadata_json,
            "target_language": profile.target_language,
            "prompt_version": profile.prompt_version,
        }
    else:
        data["channel_profile"] = None

    return data


# ── Job result sync (worker sends back records created/updated) ──────────────


class SyncResultRequest(BaseModel):
    """Worker sends back records created/updated during generation."""
    content_plan: Optional[dict] = None
    script: Optional[dict] = None
    video: Optional[dict] = None
    artifacts: dict = Field(default_factory=dict)
    clip_usages: Optional[list] = None  # V2: clip usage records for cross-job avoidance


@router.post("/jobs/{job_id}/sync")
def sync_job_result(
    job_id: int,
    req: SyncResultRequest,
    _: None = Depends(worker_auth),
    db: Session = Depends(get_db),
):
    """Sync records created/updated by the worker during generation.

    The worker runs GenerationService locally (against a temp DB) and then
    sends back the records it created/updated: ContentPlan, Script, Video,
    and updated Job artifacts.

    GUARD: Rejects sync for cancelled jobs or jobs whose domain no longer
    matches the channel's current domain.
    """
    from gpcg.core.models import ContentPlan, Script, VideoStatus
    from gpcg.core.models import Video as VideoModel

    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Cancellation + domain guard (same as submit_job_result)
    if job.status == JobStatus.cancelled.value:
        raise HTTPException(status_code=409, detail="Job has been cancelled. Sync rejected.")
    if job.user_id:
        from gpcg.core.models import ChannelProfile, ContentDomain
        profile = db.query(ChannelProfile).filter(
            ChannelProfile.user_id == job.user_id
        ).first()
        current_domain = profile.domain if profile else ContentDomain.games.value
        if job.domain and job.domain != current_domain:
            raise HTTPException(status_code=409, detail="Job belongs to a previous domain. Sync rejected.")

    # Update job artifacts
    if req.artifacts:
        # IMPORTANT: create a NEW dict (copy) so SQLAlchemy detects the change.
        # JSON columns don't track in-place mutations — assigning the same
        # object back is a no-op for the ORM.
        merged = {**_ensure_dict(job.artifacts), **req.artifacts}
        job.artifacts = merged
        has_social = "social_title" in merged
        print(f"[SYNC] job #{job_id}: {len(req.artifacts)} artifacts received, social_title present: {has_social}", flush=True)
        log.info(f"Sync job #{job_id}: merged {len(req.artifacts)} artifacts, keys={list(merged.keys())[:10]}")

    # Sync ContentPlan
    # NOTE: The content_plan id from the remote worker is the LOCAL DB id,
    # not the VPS DB id. The local DB id may collide with an existing VPS DB id.
    # So we NEVER look up by local id. Instead, we check if this job already
    # has a content_plan_id in the VPS DB, and if so, update that. Otherwise,
    # always create a new ContentPlan.
    print(f"[SYNC] job #{job_id}: content_plan={req.content_plan is not None}, script={req.script is not None}, video={req.video is not None}", flush=True)
    if req.content_plan:
        plan = None
        # Check if the job already has a content_plan_id in the VPS DB
        if job.content_plan_id:
            plan = db.query(ContentPlan).filter(ContentPlan.id == job.content_plan_id).first()
            if plan:
                for k, v in req.content_plan.items():
                    if k != "id" and hasattr(plan, k):
                        setattr(plan, k, v)
                # V3: Record per-consumer usage of the KnowledgeItem.
                # For public KIs (user_id=NULL), only creates a
                # KnowledgeItemUsage record — global status stays fresh so
                # other users can still consume it. For private KIs, also
                # marks global status=used (only the owner consumes those).
                plan_meta = req.content_plan.get("metadata_json", {}) or {}
                ki_id = plan_meta.get("knowledge_item_id")
                if ki_id:
                    from gpcg.application.knowledge_item_service import record_usage as _record_usage
                    _record_usage(db, int(ki_id), job.user_id)
                    log.info(f"Recorded usage of KI #{ki_id} by user {job.user_id} (job #{job_id}, existing plan)")
        if not plan:
            print(f"[SYNC] job #{job_id}: creating new ContentPlan in VPS DB (local id={req.content_plan.get('id')}, topic={req.content_plan.get('topic','')})", flush=True)
            # Create new ContentPlan in VPS DB (local DB id is irrelevant)
            plan = ContentPlan(
                user_id=job.user_id,
                game_id=req.content_plan.get("game_id", job.game_id),
                fact_id=req.content_plan.get("fact_id"),
                background_game_id=req.content_plan.get("background_game_id"),
                format=req.content_plan.get("format", "youtube_short"),
                target_duration=req.content_plan.get("target_duration", 60),
                target_language=req.content_plan.get("target_language", "pt-BR"),
                topic=req.content_plan.get("topic", ""),
                hook=req.content_plan.get("hook", ""),
                tone=req.content_plan.get("tone", "curious"),
                energy=req.content_plan.get("energy", 0.7),
                music_mood=req.content_plan.get("music_mood", "neutral"),
                visual_strategy=req.content_plan.get("visual_strategy", "auto"),
                metadata_json=req.content_plan.get("metadata_json", {}),
            )
            db.add(plan)
            db.flush()
            job.content_plan_id = plan.id
            # V3: Record per-consumer usage (see note above on public/private)
            plan_meta = req.content_plan.get("metadata_json", {}) or {}
            ki_id = plan_meta.get("knowledge_item_id")
            if ki_id:
                from gpcg.application.knowledge_item_service import record_usage as _record_usage
                _record_usage(db, int(ki_id), job.user_id)
                log.info(f"Recorded usage of KI #{ki_id} by user {job.user_id} (job #{job_id})")

    # Sync Script
    # NOTE: Same as ContentPlan — the script id is from the LOCAL DB.
    # Never look up by local id; check if the job's content_plan already has a script.
    if req.script:
        script = None
        if job.content_plan_id:
            script = db.query(Script).filter(Script.content_plan_id == job.content_plan_id).first()
            if script:
                for k, v in req.script.items():
                    if k != "id" and hasattr(script, k):
                        setattr(script, k, v)
        if not script and job.content_plan_id:
            script = Script(
                content_plan_id=job.content_plan_id,
                draft=req.script.get("draft", ""),
                optimized=req.script.get("optimized", ""),
                final=req.script.get("final", ""),
                status=req.script.get("status", "final"),
                language=req.script.get("language", "pt-BR"),
                char_count=req.script.get("char_count", 0),
                originality_score=req.script.get("originality_score"),
                originality_report=req.script.get("originality_report"),
                rewrite_count=req.script.get("rewrite_count", 0),
            )
            db.add(script)

    # Sync Video
    # NOTE: Same as ContentPlan — the video id is from the LOCAL DB.
    # Never look up by local id; check if the job already has a video.
    if req.video:
        video = db.query(VideoModel).filter(VideoModel.job_id == job.id).first()
        if video:
            for k, v in req.video.items():
                if k != "id" and hasattr(video, k):
                    setattr(video, k, v)
        if not video:
            # Try to get knowledge_item_id from the video dict (worker sends it)
            # Fall back to ContentPlan metadata_json
            ki_id = req.video.get("knowledge_item_id")
            if not ki_id and job.content_plan_id:
                plan = db.query(ContentPlan).filter(ContentPlan.id == job.content_plan_id).first()
                if plan and plan.metadata_json:
                    ki_id = plan.metadata_json.get("knowledge_item_id")
            video = VideoModel(
                user_id=job.user_id,
                job_id=job.id,
                content_plan_id=job.content_plan_id,
                game_id=job.game_id,
                knowledge_item_id=ki_id,
                file_path=req.video.get("file_path", ""),
                storage_key=req.video.get("storage_key"),
                duration=req.video.get("duration", 0.0),
                width=req.video.get("width", 0),
                height=req.video.get("height", 0),
                language=req.video.get("language", "pt-BR"),
                qa_score=req.video.get("qa_score", 0.0),
                qa_report=req.video.get("qa_report", {}),
                status=req.video.get("status", VideoStatus.ready.value),
                youtube_url=req.video.get("youtube_url"),
                youtube_video_id=req.video.get("youtube_video_id"),
            )
            db.add(video)
        db.flush()

    # V2: Sync clip usage records (so future jobs avoid same gameplay segments)
    if req.clip_usages:
        video = db.query(VideoModel).filter(VideoModel.job_id == job.id).first()
        if video:
            for cu_data in req.clip_usages:
                source_id = cu_data.get("source_id")
                start_sec = cu_data.get("start_sec", 0.0)
                end_sec = cu_data.get("end_sec", 0.0)
                if source_id and end_sec > start_sec:
                    # Check if already exists (avoid duplicates on re-sync)
                    existing = db.query(GameplayClipUsage).filter(
                        GameplayClipUsage.video_id == video.id,
                        GameplayClipUsage.source_id == source_id,
                        GameplayClipUsage.start_sec == start_sec,
                    ).first()
                    if not existing:
                        db.add(GameplayClipUsage(
                            video_id=video.id,
                            source_id=source_id,
                            consumer_user_id=job.user_id,
                            start_sec=start_sec,
                            end_sec=end_sec,
                            duration=end_sec - start_sec,
                        ))
            log.info(f"Synced {len(req.clip_usages)} clip usage records for job #{job_id}")

    db.commit()
    return {"ok": True}
