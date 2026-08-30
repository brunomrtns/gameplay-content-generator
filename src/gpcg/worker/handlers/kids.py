"""Kids domain job handler mixin for :class:`RemoteWorker`.

Extracted from ``remote_worker.py``. Contains:
  - Kids idea discovery (AI ideation + topic library + seasonal → sync)
  - Kids idea scoring (safety filter + scorer → sync)
  - Kids asset processing (download → FFprobe → thumbnail → semantic mapping → sync)
  - FFprobe and FFmpeg thumbnail helpers
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class KidsMixin:
    """Kids domain job processing (idea discovery, scoring, asset processing)."""

    def _process_kids_idea_discovery_job(self, job: dict) -> None:
        """Process a kids_idea_discovery job: run discovery locally → sync ideas.

        Runs AI ideation + topic library + seasonal locally (with Ollama LLM).
        Syncs the created ideas back to VPS via /jobs/{id}/sync-kids-ideas.
        """
        job_id = job["id"]
        artifacts = job.get("artifacts") or {}

        self.update_job_status(job_id, status="running", stage="discovery", progress=0.1)
        self.send_status("busy", "Descobrindo ideias Kids", job_id=job_id, activity_key="worker.activity.kids_discovering_ideas")

        # Fetch channel profile from VPS (worker-auth endpoint)
        try:
            resp = self.client.get(f"/api/workers/channel-profile/{job['user_id']}")
            if resp.status_code != 200:
                self.submit_job_result(job_id, status="failed", error="Could not fetch channel profile")
                return
            profile_data = resp.json()
        except Exception as e:
            self.submit_job_result(job_id, status="failed", error=f"Failed to fetch profile: {e}")
            return

        # Build a lightweight profile object for discovery
        from gpcg.domains.kids.discovery import KidsIdeaDiscovery
        from gpcg.infrastructure.llm import LLMClient

        class _Profile:
            """Minimal profile stub for headless discovery."""
            def __init__(self, data: dict):
                self.metadata_json = data.get("metadata_json") or data.get("metadata") or {}
                self.channel_description = data.get("channel_description", "")
                self.niche = data.get("niche", "")
                self.target_audience = data.get("target_audience", "")
                self.tone_of_voice = data.get("tone_of_voice", "")
                self.narrative_style = data.get("narrative_style", "")
                self.content_goals = data.get("content_goals", "")
                self.special_rules = data.get("special_rules", "")

            def to_prompt_context(self) -> str:
                parts = []
                if self.channel_description:
                    parts.append(f"Channel: {self.channel_description}")
                if self.niche:
                    parts.append(f"Niche: {self.niche}")
                if self.target_audience:
                    parts.append(f"Audience: {self.target_audience}")
                if self.tone_of_voice:
                    parts.append(f"Tone: {self.tone_of_voice}")
                if self.narrative_style:
                    parts.append(f"Style: {self.narrative_style}")
                if self.content_goals:
                    parts.append(f"Goals: {self.content_goals}")
                return " | ".join(parts) if parts else ""

        profile = _Profile(profile_data)

        # Init LLM (local Ollama)
        try:
            llm = LLMClient()
        except Exception as e:
            log.warning(f"LLM init failed for discovery: {e}")
            llm = None

        self.update_job_status(job_id, status="running", stage="discovery", progress=0.3)

        # Run discovery locally — no DB session, collect results in memory
        discovery = KidsIdeaDiscovery(llm=llm)

        # We need a DB session for create_idea's dedup check.
        # Use a local temp SQLite DB (same pattern as generation jobs).
        # But discovery's create_idea checks for duplicates in the DB,
        # and we don't have the VPS's ideas locally. So we'll collect
        # the raw ideas and send them to VPS for dedup + storage.
        categories = artifacts.get("categories")
        ideas_per_category = artifacts.get("ideas_per_category", 3)
        include_seasonal = artifacts.get("include_seasonal", True)
        include_topic_library = artifacts.get("include_topic_library", True)

        # Collect ideas without DB persistence
        sync_ideas: list[dict] = []

        from gpcg.domains.kids.topic_library import get_all_categories, get_category, get_seeds_for_category
        from gpcg.domains.kids.seasonal_calendar import get_active_seasonal

        # Determine categories
        if categories:
            cats = [get_category(c) for c in categories if get_category(c)]
            cats = [c for c in cats if c is not None]
        else:
            cats = get_all_categories()

        age_range = discovery._get_age_range(profile)
        channel_context = profile.to_prompt_context()

        # 1. AI Ideation
        self.update_job_status(job_id, status="running", stage="discovery", progress=0.4)
        for cat in cats:
            try:
                ai_ideas = discovery._ai_ideation(
                    cat, age_range, channel_context, ideas_per_category
                )
                for idea_data in ai_ideas:
                    sync_ideas.append({
                        "title": idea_data["title"],
                        "description": idea_data.get("description", ""),
                        "category": idea_data.get("category", cat.name),
                        "suggested_age_range": idea_data.get("suggested_age_range", age_range),
                        "source": "ai_ideation",
                        "source_metadata": {
                            "category": cat.name,
                            "channel_context": channel_context[:200],
                        },
                    })
            except Exception as e:
                log.warning(f"discovery.ai_ideation_failed: category={cat.name}, error={e}")

        # 2. Topic Library seeds
        self.update_job_status(job_id, status="running", stage="discovery", progress=0.6)
        if include_topic_library:
            for cat in cats:
                for seed in get_seeds_for_category(cat.name):
                    sync_ideas.append({
                        "title": seed.title_hint,
                        "description": seed.description,
                        "category": cat.name,
                        "suggested_age_range": age_range,
                        "source": "topic_library",
                        "source_metadata": {"category": cat.name, "seed": True},
                    })

        # 3. Seasonal
        self.update_job_status(job_id, status="running", stage="discovery", progress=0.8)
        if include_seasonal:
            seasonal_entries = get_active_seasonal()
            for entry in seasonal_entries:
                try:
                    seasonal_ideas = discovery._seasonal_ideation(
                        entry, age_range, channel_context
                    )
                    for idea_data in seasonal_ideas:
                        sync_ideas.append({
                            "title": idea_data["title"],
                            "description": idea_data.get("description", ""),
                            "category": idea_data.get("category", entry.category),
                            "suggested_age_range": idea_data.get("suggested_age_range", age_range),
                            "source": "seasonal",
                            "source_metadata": {
                                "seasonal_entry": entry.name,
                                "date": entry.date,
                            },
                        })
                except Exception as e:
                    log.warning(f"discovery.seasonal_failed: entry={entry.name}, error={e}")

        log.info(f"Kids discovery: collected {len(sync_ideas)} ideas locally")

        # 4. Safety + Scoring (run locally with LLM, same as content_collect does)
        self.update_job_status(job_id, status="running", stage="scoring", progress=0.85)
        from gpcg.domains.kids.safety_filter import KidsSafetyFilter
        from gpcg.domains.kids.scorer import KidsScorer

        strictness = float(
            (profile.metadata_json or {}).get("kids_safety_strictness", 0.7)
        )
        safety_filter = KidsSafetyFilter(llm=llm)
        scorer = KidsScorer(llm=llm)

        evaluated_ideas: list[dict] = []
        rejected_count = 0
        for idea_data in sync_ideas:
            try:
                safety_result = safety_filter.review(
                    title=idea_data["title"],
                    description=idea_data.get("description", ""),
                    age_range=idea_data.get("suggested_age_range", age_range),
                    strictness=strictness,
                )
                if not safety_result.safe:
                    rejected_count += 1
                    continue

                score_result = scorer.score(
                    title=idea_data["title"],
                    description=idea_data.get("description", ""),
                    age_range=idea_data.get("suggested_age_range", age_range),
                    category=idea_data.get("category", ""),
                    channel_context=channel_context,
                )

                idea_data["safety_score"] = safety_result.safety_score
                idea_data["safety_flags"] = safety_result.flags
                idea_data["safety_reviewed"] = True
                idea_data["editorial_score"] = score_result.editorial_score_0_100
                idea_data["age_fit_score"] = score_result.age_fit
                idea_data["educational_value"] = score_result.educational_value
                idea_data["curiosity_score"] = score_result.curiosity
                idea_data["visual_potential"] = score_result.visual_potential
                idea_data["final_score"] = score_result.final_score
                idea_data["score_breakdown"] = score_result.breakdown
                idea_data["evaluated"] = True
                evaluated_ideas.append(idea_data)
            except Exception as e:
                log.warning(f"discovery.score_failed: title='{idea_data['title'][:40]}', error={e}")
                # Keep the idea as discovered (no score) — better than dropping it
                idea_data["evaluated"] = False
                evaluated_ideas.append(idea_data)

        log.info(
            f"Kids discovery: {len(evaluated_ideas)} evaluated, "
            f"{rejected_count} rejected by safety"
        )

        # Sync ideas to VPS
        self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
        try:
            sync_resp = self.client.post(
                f"/api/jobs/{job_id}/sync-kids-ideas",
                json={"ideas": evaluated_ideas},
            )
            sync_resp.raise_for_status()
            sync_data = sync_resp.json()
            log.info(f"Kids discovery synced to VPS: {sync_data}")
        except Exception as e:
            log.error(f"Failed to sync kids ideas to VPS: {e}")
            self.submit_job_result(job_id, status="failed", error=f"Sync failed: {e}")
            return

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "discovery_completed": True,
            "ideas_collected": len(sync_ideas),
            "created_count": sync_data.get("created_count", 0),
            "skipped_count": sync_data.get("skipped_count", 0),
        })
        log.info(f"Kids discovery job #{job_id} completed: {len(sync_ideas)} ideas collected")

    def _process_kids_idea_score_job(self, job: dict) -> None:
        """Process a kids_idea_score job: run safety + scoring locally → sync.

        Runs KidsSafetyFilter (LLM review) and KidsScorer locally (with Ollama).
        Syncs the results back to VPS via /jobs/{id}/sync-kids-score.
        """
        job_id = job["id"]
        artifacts = job.get("artifacts") or {}
        idea_id = artifacts.get("idea_id")
        if not idea_id:
            self.submit_job_result(job_id, status="failed", error="No idea_id in job artifacts")
            return

        self.update_job_status(job_id, status="running", stage="scoring", progress=0.1)
        self.send_status("busy", f"Avaliando ideia Kids #{idea_id}", job_id=job_id, activity_key="worker.activity.kids_evaluating_idea")

        # Fetch the idea from VPS (worker-auth endpoint)
        try:
            resp = self.client.get(f"/api/workers/kids-ideas/{idea_id}")
            if resp.status_code != 200:
                self.submit_job_result(job_id, status="failed", error=f"Could not fetch idea #{idea_id}")
                return
            idea_data = resp.json()
        except Exception as e:
            self.submit_job_result(job_id, status="failed", error=f"Failed to fetch idea: {e}")
            return

        # Fetch channel profile from VPS (worker-auth endpoint)
        try:
            resp = self.client.get(f"/api/workers/channel-profile/{job['user_id']}")
            if resp.status_code != 200:
                self.submit_job_result(job_id, status="failed", error="Could not fetch channel profile")
                return
            profile_data = resp.json()
        except Exception as e:
            self.submit_job_result(job_id, status="failed", error=f"Failed to fetch profile: {e}")
            return

        meta = profile_data.get("metadata_json") or profile_data.get("metadata") or {}
        strictness = float(meta.get("kids_safety_strictness", 0.7))
        age_range = idea_data.get("suggested_age_range") or str(
            meta.get("age_range", meta.get("kids_age_range", "3-6"))
        )

        # Build channel context
        channel_context = " | ".join(
            f"{k}: {v}" for k, v in [
                ("Channel", profile_data.get("channel_description", "")),
                ("Niche", profile_data.get("niche", "")),
                ("Audience", profile_data.get("target_audience", "")),
                ("Tone", profile_data.get("tone_of_voice", "")),
            ] if v
        )

        # Init LLM (local Ollama)
        try:
            llm = LLMClient()
        except Exception as e:
            log.warning(f"LLM init failed for scoring: {e}")
            llm = None

        self.update_job_status(job_id, status="running", stage="scoring", progress=0.3)

        # 1. Safety review
        from gpcg.domains.kids.safety_filter import KidsSafetyFilter
        safety_filter = KidsSafetyFilter(llm=llm)
        safety_result = safety_filter.review(
            title=idea_data["title"],
            description=idea_data.get("description", ""),
            age_range=age_range,
            strictness=strictness,
        )

        safety_dict = {
            "safe": safety_result.safe,
            "safety_score": safety_result.safety_score,
            "flags": safety_result.flags,
            "age_suitability": safety_result.age_suitability,
            "reason": safety_result.reason,
        }

        if not safety_result.safe:
            # Auto-reject — no need for scoring
            self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
            try:
                sync_resp = self.client.post(
                    f"/api/jobs/{job_id}/sync-kids-score",
                    json={
                        "idea_id": idea_id,
                        "safety": safety_dict,
                        "scoring": {},
                        "status": "rejected",
                    },
                )
                sync_resp.raise_for_status()
            except Exception as e:
                self.submit_job_result(job_id, status="failed", error=f"Sync failed: {e}")
                return

            self.update_job_status(job_id, status="running", stage="done", progress=1.0)
            self.submit_job_result(job_id, status="completed", artifacts={
                "idea_id": idea_id,
                "safe": False,
                "rejected": True,
            })
            log.info(f"Kids scoring job #{job_id}: idea #{idea_id} rejected (safety)")
            return

        # 2. Scoring
        self.update_job_status(job_id, status="running", stage="scoring", progress=0.6)
        from gpcg.domains.kids.scorer import KidsScorer
        scorer = KidsScorer(llm=llm)
        score_result = scorer.score(
            title=idea_data["title"],
            description=idea_data.get("description", ""),
            age_range=age_range,
            category=idea_data.get("category", ""),
            channel_context=channel_context,
        )

        scoring_dict = {
            "editorial_quality": score_result.editorial_quality,
            "age_fit": score_result.age_fit,
            "educational_value": score_result.educational_value,
            "curiosity": score_result.curiosity,
            "visual_potential": score_result.visual_potential,
            "simplicity": score_result.simplicity,
            "final_score": score_result.final_score,
            "editorial_score_0_100": score_result.editorial_score_0_100,
            "reason": score_result.reason,
            "breakdown": score_result.breakdown,
        }

        # Sync results to VPS
        self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
        try:
            sync_resp = self.client.post(
                f"/api/jobs/{job_id}/sync-kids-score",
                json={
                    "idea_id": idea_id,
                    "safety": safety_dict,
                    "scoring": scoring_dict,
                    "status": "evaluated",
                },
            )
            sync_resp.raise_for_status()
        except Exception as e:
            self.submit_job_result(job_id, status="failed", error=f"Sync failed: {e}")
            return

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "idea_id": idea_id,
            "safe": True,
            "final_score": score_result.final_score,
            "editorial_score_0_100": score_result.editorial_score_0_100,
        })
        log.info(f"Kids scoring job #{job_id}: idea #{idea_id} scored (final={score_result.final_score:.2f})")

    # ── Kids asset processing: video → FFprobe + thumbnail ────────────────────

    def _process_kids_asset_process_job(self, job: dict) -> None:
        """Process a kids_asset_process job: download video → FFprobe → thumbnail → sync.

        Downloads the uploaded video from the VPS, runs FFprobe to extract
        metadata (duration, dimensions, codec, audio presence), generates
        a thumbnail with FFmpeg, uploads the thumbnail back, and syncs
        the metadata to the VPS so the StoryAsset is marked ``ready``.

        If FFprobe/FFmpeg are not available, the asset is still marked
        ready with zero metadata — the pipeline can still use the raw
        video file. This is a graceful degradation, not a hard failure.
        """
        job_id = job["id"]
        artifacts = job.get("artifacts") or {}
        asset_id = artifacts.get("asset_id")
        if not asset_id:
            self.submit_job_result(job_id, status="failed", error="No asset_id in job artifacts")
            return

        filename = artifacts.get("filename", f"asset_{asset_id}")
        file_hash = artifacts.get("file_hash", "")

        self.update_job_status(job_id, status="running", stage="download", progress=0.1)
        self.send_status("busy", f"Processando mídia Kids: {filename}", job_id=job_id, activity_key="worker.activity.kids_processing_media")

        # Download the video from VPS
        kids_dir = self.storage_root / "kids_assets"
        kids_dir.mkdir(parents=True, exist_ok=True)

        # Use storage_key from artifacts if available, else derive from hash+filename
        storage_key = artifacts.get("storage_key", f"{file_hash[:8]}_{filename}")
        local_path = kids_dir / storage_key

        if not local_path.exists():
            try:
                log.info(f"Downloading Kids video asset #{asset_id} ({filename}) from VPS...")
                resp = self.client.get(f"/api/kids/assets/{asset_id}/download")
                resp.raise_for_status()
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                log.info(f"Downloaded Kids video asset #{asset_id} → {local_path}")
            except Exception as e:
                error = f"Failed to download asset #{asset_id}: {e}"
                log.error(error)
                self._submit_kids_asset_error(job_id, asset_id, error)
                return
        else:
            log.info(f"Kids video asset #{asset_id} already cached locally: {local_path}")

        # Verify checksum if we have a hash
        if file_hash:
            sha256 = hashlib.sha256()
            with open(local_path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    sha256.update(chunk)
            actual_hash = sha256.hexdigest()
            if actual_hash.lower() != file_hash.lower():
                error = f"Checksum mismatch for asset #{asset_id}: expected {file_hash[:16]}... got {actual_hash[:16]}..."
                log.error(error)
                self._submit_kids_asset_error(job_id, asset_id, error)
                return

        self.update_job_status(job_id, status="running", stage="mapping", progress=0.4)

        # Run FFprobe to extract metadata
        width, height, duration, codec, has_audio = 0, 0, 0.0, "", False
        try:
            width, height, duration, codec, has_audio = self._ffprobe_video(local_path)
            log.info(
                f"FFprobe asset #{asset_id}: {width}x{height} {duration:.1f}s "
                f"codec={codec} audio={has_audio}"
            )
        except Exception as e:
            log.warning(f"FFprobe failed for asset #{asset_id}: {e} — continuing with zero metadata")

        self.update_job_status(job_id, status="running", stage="render", progress=0.7)

        # Generate thumbnail with FFmpeg (best-effort, non-fatal)
        thumbnail_key = ""
        try:
            thumbnail_key = self._generate_thumbnail(local_path, asset_id, kids_dir)
            if thumbnail_key:
                # Upload thumbnail to VPS
                thumb_path = kids_dir / thumbnail_key
                with open(thumb_path, "rb") as f:
                    files = {"file": (thumbnail_key, f, "image/jpeg")}
                    resp = self.client.post(
                        f"/api/kids/assets/{asset_id}/thumbnail",
                        files=files,
                    )
                    resp.raise_for_status()
                log.info(f"Uploaded thumbnail for asset #{asset_id}: {thumbnail_key}")
        except Exception as e:
            log.warning(f"Thumbnail generation failed for asset #{asset_id}: {e} — non-fatal")

        # Sync metadata to VPS (FFprobe results first — marks as "mapping")
        self.update_job_status(job_id, status="running", stage="sync", progress=0.5)
        try:
            resp = self.client.post(
                f"/api/kids/assets/{asset_id}/process-result",
                json={
                    "asset_id": asset_id,
                    "width": width,
                    "height": height,
                    "duration": duration,
                    "codec": codec,
                    "has_audio": has_audio,
                    "thumbnail_key": thumbnail_key,
                    "error": "",
                },
            )
            resp.raise_for_status()
        except Exception as e:
            error = f"Failed to sync process result: {e}"
            log.error(error)
            self._submit_kids_asset_error(job_id, asset_id, error)
            return

        # ── Semantic mapping (VLM + ASR → KidsMediaEvent) ───────────────
        # Same pipeline as GameplayAnalyzer in Games: the worker runs the
        # analyzer locally (GPU) to produce events that index the video
        # for semantic selection by KidsMediaRetriever.
        self.update_job_status(job_id, status="running", stage="mapping", progress=0.6)
        self.send_status("busy", f"Mapeando mídia Kids: {filename}", job_id=job_id, activity_key="worker.activity.kids_mapping_media")

        # Checkpoint: reuse cached analysis if available
        analysis_cache_path = self.storage_root / "mapped" / f"kids_asset_{asset_id}_analysis.json"
        timeline = None
        if analysis_cache_path.exists():
            try:
                from gpcg.domain.gameplay_events import EventTimeline
                cached = EventTimeline.from_json(analysis_cache_path.read_text())
                if cached.event_count > 0:
                    log.info(
                        f"Reusing cached Kids analysis for asset #{asset_id} "
                        f"({cached.event_count} events) — skipping VLM/ASR"
                    )
                    timeline = cached
            except Exception as e:
                log.warning(f"Cached Kids analysis JSON invalid, will re-analyze: {e}")
                timeline = None

        events_data: list[dict] = []
        if timeline is not None:
            from gpcg.application.kids_media_analyzer import kids_media_events_from_timeline
            events_data = kids_media_events_from_timeline(timeline, asset_id)
        else:
            try:
                from gpcg.application.kids_media_analyzer import (
                    KidsMediaAnalyzer,
                    kids_media_events_from_timeline,
                )
                analyzer = KidsMediaAnalyzer()
                timeline = analyzer.analyze(
                    local_path,
                    asset_id=asset_id,
                    progress_callback=lambda stage, pct: self.update_job_status(
                        job_id, status="running", stage="mapping",
                        progress=0.6 + pct * 0.3,
                    ),
                )
                events_data = kids_media_events_from_timeline(timeline, asset_id)

                # Save analysis JSON locally for checkpoint/resume
                analysis_cache_path.parent.mkdir(parents=True, exist_ok=True)
                analysis_cache_path.write_text(timeline.to_json(indent=2))

                log.info(
                    f"Kids media mapping: asset #{asset_id} → {len(events_data)} events "
                    f"(version={timeline.analysis_version}, model={timeline.vision_model})"
                )
            except Exception as e:
                log.warning(
                    f"Kids media mapping failed for asset #{asset_id}: {e} — "
                    f"continuing with metadata only (no semantic events). "
                    f"The asset will be ready but without semantic indexing."
                )

        # Sync mapping events to VPS
        self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
        if events_data:
            try:
                resp = self.client.post(
                    f"/api/kids/assets/{asset_id}/mapping-result",
                    json={
                        "asset_id": asset_id,
                        "events": events_data,
                        "analysis_version": events_data[0].get("analysis_version", "v1") if events_data else "v1",
                    },
                )
                resp.raise_for_status()
                log.info(f"Synced {len(events_data)} Kids media events for asset #{asset_id}")
            except Exception as e:
                log.warning(f"Failed to sync mapping events for asset #{asset_id}: {e} — non-fatal")

        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "asset_id": asset_id,
            "media_kind": "video",
            "width": width,
            "height": height,
            "duration": duration,
            "has_audio": has_audio,
            "thumbnail_key": thumbnail_key,
            "event_count": len(events_data),
        })
        log.info(f"Kids asset process job #{job_id}: asset #{asset_id} ready ({len(events_data)} events)")

    def _submit_kids_asset_error(self, job_id: int, asset_id: int, error: str) -> None:
        """Submit a processing error to the VPS and mark the job as failed."""
        try:
            self.client.post(
                f"/api/kids/assets/{asset_id}/process-result",
                json={"asset_id": asset_id, "error": error},
            )
        except Exception:
            pass  # Best-effort — the job failure is reported below
        self.submit_job_result(job_id, status="failed", error=error)

    @staticmethod
    def _ffprobe_video(path: Path) -> tuple:
        """Run FFprobe on a video file and return (width, height, duration, codec, has_audio).

        Uses subprocess to call ffprobe (must be installed on the worker).
        Returns zeros/empty if ffprobe is not available or fails.
        """
        import subprocess as _sp
        import json as _json

        # Check if ffprobe is available
        try:
            _sp.run(["ffprobe", "-version"], capture_output=True, check=True)
        except (FileNotFoundError, _sp.CalledProcessError):
            raise RuntimeError("ffprobe not available on worker")

        cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ]
        result = _sp.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(f"ffprobe failed: {result.stderr[:200]}")

        data = _json.loads(result.stdout)
        width = height = 0
        duration = 0.0
        codec = ""
        has_audio = False

        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                width = int(stream.get("width", 0))
                height = int(stream.get("height", 0))
                codec = stream.get("codec_name", "")
            elif stream.get("codec_type") == "audio":
                has_audio = True

        fmt = data.get("format", {})
        duration = float(fmt.get("duration", 0.0))

        return width, height, duration, codec, has_audio

    @staticmethod
    def _generate_thumbnail(video_path: Path, asset_id: int, output_dir: Path) -> str:
        """Generate a thumbnail from a video using FFmpeg.

        Extracts a frame at 1 second (or 10% of duration for short clips).
        Returns the thumbnail filename (relative to output_dir), or empty
        string if generation failed.
        """
        import subprocess as _sp

        try:
            _sp.run(["ffmpeg", "-version"], capture_output=True, check=True)
        except (FileNotFoundError, _sp.CalledProcessError):
            return ""

        thumb_name = f"thumb_{asset_id}.jpg"
        thumb_path = output_dir / thumb_name

        # Extract frame at 1s (or 0.5s for very short videos)
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-ss", "00:00:01", "-frames:v", "1",
            "-vf", "scale=320:-1",
            "-q:v", "2",
            str(thumb_path),
        ]
        result = _sp.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0 or not thumb_path.exists():
            # Try at 0s (some videos are shorter than 1s)
            cmd = [
                "ffmpeg", "-y", "-i", str(video_path),
                "-ss", "00:00:00", "-frames:v", "1",
                "-vf", "scale=320:-1",
                "-q:v", "2",
                str(thumb_path),
            ]
            result = _sp.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0 or not thumb_path.exists():
                return ""

        return thumb_name if thumb_path.exists() else ""
