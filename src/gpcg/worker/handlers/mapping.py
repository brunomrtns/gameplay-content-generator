"""Mapping job handler mixin for :class:`RemoteWorker`.

Extracted from ``remote_worker.py``. Contains the gameplay mapping pipeline
(download → confirm → VLM game resolution → GameplayAnalyzer → submit events)
and the VLM resolution helpers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..config import JobCancelledError

log = logging.getLogger(__name__)


class MappingMixin:
    """Mapping job processing and VLM game resolution."""

    def _try_vlm_resolution(
        self,
        source_id: int,
        local_path: Path,
        source: dict,
    ) -> Optional[int]:
        """Run full game resolution (L1→L2→L3) locally and report to VPS.

        The VPS does NOT attempt game resolution — it just stores the upload
        and creates a mapping job. The worker (with GPU + Ollama + the video)
        runs the full hierarchical resolver:
          L1: deterministic (filename → slug/alias registry)
          L2: prior (capture_source → historical game association)
          L3: VLM (sampled frames → gemma3:12b identification)

        Fetches the game registry from VPS, builds a temp SQLite DB, runs
        resolve(), and reports the result back via
        POST /gameplays/{source_id}/resolve-game.

        Returns the resolved game_id if successful, None otherwise.
        """
        try:
            from gpcg.domain.game_resolver import resolve
            from gpcg.infrastructure.llm import LLMClient
        except ImportError as e:
            log.warning(f"Game resolver modules not available: {e}")
            return None

        # L1/L2 work without Ollama. L3 needs it.
        llm = None
        if self._ollama_available():
            llm = LLMClient()
        else:
            log.info("Ollama not available — will try L1/L2 only (no VLM)")

        self.send_status("busy", f"Identificando jogo — {source['filename']}", activity_key="worker.activity.identifying_game")
        log.info(f"Running game resolution (L1→L2→L3) for source #{source_id}")

        try:
            # Fetch game registry from VPS and build a temp DB
            session = self._build_resolver_session()
            if session is None:
                log.warning("Could not build resolver session — skipping game resolution")
                return None

            try:
                result = resolve(local_path, local_path.name, session, llm=llm)
            finally:
                session.close()

            if not result or not result.game_name or result.confidence < 0.5:
                log.info(
                    f"Game resolution inconclusive for #{source_id}: "
                    f"game={result.game_name if result else 'None'} "
                    f"conf={result.confidence if result else 0} "
                    f"method={result.method if result else 'none'}"
                )
                return None

            log.info(
                f"Resolved #{source_id} → '{result.game_name}' "
                f"(method={result.method}, conf={result.confidence:.2f})"
            )

            # Report to VPS
            resp = self.client.post(
                f"/api/gameplays/{source_id}/resolve-game",
                json={
                    "game_name": result.game_name,
                    "method": result.method,
                    "confidence": result.confidence,
                    "notes": result.notes,
                    "capture_source": source.get("capture_source") or result.capture_source,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("updated"):
                    log.info(
                        f"VPS updated source #{source_id} → game_id={data.get('game_id')} "
                        f"({data.get('game_name')})"
                    )
                    return data.get("game_id")
                else:
                    log.info(f"VPS did not update source #{source_id}: {data.get('reason')}")
            else:
                log.warning(f"VPS rejected game resolution for #{source_id}: {resp.status_code}")

        except Exception as e:
            log.error(f"Game resolution failed for #{source_id}: {e}")

        return None

    def _build_resolver_session(self):
        """Build a temp SQLite DB with games + aliases from VPS for the resolver.

        Returns a SQLAlchemy session, or None on failure.
        """
        import tempfile
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        try:
            resp = self.client.get("/api/games/registry")
            if resp.status_code != 200:
                log.warning(f"Failed to fetch game registry: {resp.status_code}")
                return None
            data = resp.json()
        except Exception as e:
            log.warning(f"Error fetching game registry: {e}")
            return None

        from gpcg.core.models import Base
        from gpcg.domains.games.models import Game, GameAlias
        import gpcg.core.models  # noqa: side effect: register all tables
        import gpcg.domains.games.models  # noqa: side effect: register games tables

        tmpdir = tempfile.mkdtemp(prefix="gpcg_resolver_")
        db_path = Path(tmpdir) / "resolver.db"
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        session = SessionLocal()

        try:
            for g in data.get("games", []):
                session.add(Game(
                    id=g["id"],
                    canonical_name=g["canonical_name"],
                    slug=g.get("slug", ""),
                    camera_type=g.get("camera_type", "unknown"),
                ))
            for a in data.get("aliases", []):
                session.add(GameAlias(
                    game_id=a["game_id"],
                    alias=a["alias"],
                ))
            session.commit()
            log.info(
                f"Resolver DB: {len(data.get('games', []))} games, "
                f"{len(data.get('aliases', []))} aliases"
            )
            return session
        except Exception:
            session.close()
            raise

    def _ollama_available(self) -> bool:
        """Check if Ollama is running locally."""
        import requests
        try:
            r = requests.get(f"{self.config.ollama_url}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def submit_mapping_result(
        self,
        source_id: int,
        events: list[dict],
        analysis_version: str = "v1",
        config_hash: str = "",
        compatibility: Optional[dict] = None,
        media_info: Optional[dict] = None,
    ) -> dict:
        """Send gameplay analysis events to VPS.

        Args:
            media_info: Optional dict with duration, width, height, fps, codec,
                has_audio from ffprobe. Synced back to the GameplaySource so
                the VPS has accurate media metadata without probing the file.
        """
        payload = {
            "events": events,
            "analysis_version": analysis_version,
            "config_hash": config_hash,
            "compatibility": compatibility or {},
        }
        if media_info:
            payload.update(media_info)
        resp = self.client.post(
            f"/api/gameplays/{source_id}/mapping-result",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    def _process_mapping_job(self, job: dict) -> None:
        """Process a mapping job: download → confirm → analyze → report.

        Runs GameplayAnalyzer locally (VLM + ASR + merge + interesting score).
        Sends only the structured event data to the VPS — never frames, crops,
        caches, or intermediate files. Those stay on local storage.
        """
        job_id = job["id"]
        source = job.get("gameplay_source")
        if not source:
            self.submit_job_result(job_id, status="failed", error="No gameplay source in job")
            return

        source_id = source["id"]
        filename = source["filename"]
        expected_hash = source.get("file_hash", "")
        expected_size = source.get("file_size", 0)

        # Local path: /ToshibaHD/gpcg/gameplays/{source_id}_{filename}
        local_path = self.storage_root / "gameplays" / f"{source_id}_{filename}"

        # Cooperative cancellation check before heavy work
        if self.check_job_cancelled(job_id):
            raise JobCancelledError(job_id)

        # Stage 1: Download (skip if file already exists locally with matching hash)
        self.update_job_status(job_id, status="running", stage="download", progress=0.05)

        if local_path.exists() and local_path.stat().st_size > 0:
            # File already exists locally — verify checksum before reusing
            if self._verify_local_file(local_path, expected_hash):
                log.info(f"Reusing existing local file for {filename} (checksum OK)")
            else:
                log.warning(f"Local file exists but checksum mismatch — re-downloading")
                local_path.unlink()
                local_path = self.download_gameplay(source)
        else:
            local_path = self.download_gameplay(source)

        # Stage 2: Confirm download (checksum) → VPS deletes temp file
        # Skip if temp file was already deleted (re-processing a job)
        self.update_job_status(job_id, status="running", stage="confirm_download", progress=0.10)
        try:
            confirmed = self.confirm_download(source, local_path)
            if not confirmed:
                self.submit_job_result(job_id, status="failed", error="Checksum mismatch")
                return
        except Exception as e:
            # Temp file may have been deleted already (re-processing after restart)
            # If local file exists and hash is valid, continue with mapping
            if local_path.exists() and self._verify_local_file(local_path, expected_hash):
                log.warning(f"Confirm-download failed (temp already deleted?): {e} — continuing with local file")
            else:
                self.submit_job_result(job_id, status="failed", error=f"Download confirm failed: {e}")
                return

        # Stage 2b: VLM game resolution (if not already resolved with high confidence)
        game_id = source.get("game_id")
        resolution_confidence = source.get("resolution_confidence", 0.0) or 0.0
        resolution_method = source.get("resolution_method", "unknown") or "unknown"

        if not game_id or resolution_confidence < 0.6:
            log.info(
                f"Source #{source_id} needs VLM game resolution "
                f"(game_id={game_id}, method={resolution_method}, conf={resolution_confidence})"
            )
            game_id = self._try_vlm_resolution(source_id, local_path, source)
        else:
            log.debug(f"Source #{source_id} already resolved (game_id={game_id}, conf={resolution_confidence})")

        # Stage 3: Run GameplayAnalyzer locally (or reuse existing analysis)
        self.update_job_status(job_id, status="running", stage="mapping", progress=0.15)
        self.send_status("busy", f"Mapeando {source['filename']}", job_id=job_id, activity_key="worker.activity.mapping_gameplay")

        from gpcg.application.gameplay_analyzer import GameplayAnalyzer
        from gpcg.domain.gameplay_events import AnalysisConfig, EventTimeline
        from gpcg.config import get_settings

        settings = get_settings()

        # Determine camera_type from the game (if linked)
        camera_type = "unknown"
        if game_id:
            # Fetch game info from VPS to get camera_type
            try:
                resp = self.client.get(f"/api/jobs/{job_id}/data")
                if resp.status_code == 200:
                    job_data = resp.json()
                    game = job_data.get("game")
                    if game and game.get("camera_type") and game["camera_type"] != "unknown":
                        camera_type = game["camera_type"]
            except Exception:
                pass  # fallback to "unknown"

        # Build analysis config from settings
        config = AnalysisConfig(
            coarse_segment_sec=settings.gpcg_gameplay_coarse_segment_sec,
            refine_interval_sec=settings.gpcg_gameplay_refine_interval_sec,
            activity_threshold=settings.gpcg_gameplay_activity_threshold,
            high_activity_threshold=settings.gpcg_gameplay_high_activity_threshold,
            ultra_refine_interval_sec=settings.gpcg_gameplay_ultra_refine_interval_sec,
            interesting_threshold=settings.gpcg_gameplay_interesting_threshold,
            vlm_batch_size=settings.gpcg_gameplay_vlm_batch_size,
            analysis_version=settings.gpcg_gameplay_analysis_version,
            vision_model=settings.gpcg_gameplay_vision_model,
            asr_model=settings.gpcg_gameplay_asr_model,
            asr_device=settings.gpcg_gameplay_asr_device,
            asr_compute_type=settings.gpcg_gameplay_asr_compute_type,
            enable_asr=settings.gpcg_gameplay_analysis_enabled,
            enable_interesting_score=True,
        )

        # ── Checkpoint: reuse existing analysis if valid ──────────────────
        # If a previous analysis JSON exists for this source and was produced
        # with the same config_hash and analysis_version, skip re-analysis.
        # This saves hours of VLM/ASR work when a job is requeued after a
        # worker shutdown.
        analysis_json_path = self.storage_root / "mapped" / f"source_{source['id']}_analysis.json"
        timeline = None

        if analysis_json_path.exists():
            try:
                cached = EventTimeline.from_json(analysis_json_path.read_text())
                current_hash = config.to_hash()
                if (cached.analysis_version == config.analysis_version
                        and cached.config_hash == current_hash
                        and cached.event_count > 0):
                    log.info(
                        f"Reusing cached analysis for source #{source['id']} "
                        f"({cached.event_count} events, version={cached.analysis_version}) "
                        f"— skipping VLM/ASR"
                    )
                    timeline = cached
                else:
                    log.info(
                        f"Cached analysis for source #{source['id']} is outdated "
                        f"(version/hash changed) — will re-analyze"
                    )
            except Exception as e:
                log.warning(f"Cached analysis JSON invalid, will re-analyze: {e}")
                timeline = None

        if timeline is None:
            analyzer = GameplayAnalyzer(camera_type=camera_type, config=config)

            # Progress callback: update VPS with mapping progress
            def _progress(stage: str, pct: float) -> None:
                # Map analyzer stage to 0.15-0.90 range
                mapped = 0.15 + pct * 0.75
                self.update_job_status(job_id, status="running", stage="mapping", progress=mapped)

            log.info(f"Starting GameplayAnalyzer on {local_path.name} (camera_type={camera_type})")
            timeline = analyzer.analyze(
                local_path,
                source_id=source["id"],
                progress_callback=_progress,
            )

            log.info(
                f"Analysis complete: {timeline.event_count} events "
                f"(confident={len(timeline.confident_events)}, "
                f"interesting={len(timeline.interesting_events)})"
            )

            # Save analysis JSON locally (for debugging/reference + checkpoint)
            analysis_json_path.parent.mkdir(parents=True, exist_ok=True)
            analysis_json_path.write_text(timeline.to_json(indent=2))

        # Stage 4: Submit mapping result (events + media metadata)
        self.update_job_status(job_id, status="running", stage="mapping", progress=0.90)
        events = [e.to_dict() for e in timeline.events]

        # Compute compatibility flags
        compatibility = {"game_related": True, "general_topic": True}

        # Send media metadata (duration, has_audio) back to VPS so the
        # source record has accurate info without the VPS probing the
        # (now-deleted) temp file. EventTimeline has duration + has_audio.
        media_info = {
            "duration": timeline.duration,
            "has_audio": timeline.has_audio,
        }

        self.submit_mapping_result(
            source_id=source["id"],
            events=events,
            analysis_version=timeline.analysis_version,
            config_hash=timeline.config_hash,
            compatibility=compatibility,
            media_info=media_info,
        )

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "mapping_completed": True,
            "analysis_version": timeline.analysis_version,
            "config_hash": timeline.config_hash,
            "events_count": len(events),
            "vision_model": timeline.vision_model,
            "asr_model": timeline.asr_model,
            "duration": timeline.duration,
        })
        log.info(f"Mapping job #{job_id} completed: {len(events)} events")

        # Keep the gameplay file locally — generation jobs need the actual video
        # to extract clips and render. Previously this was deleted to save HD
        # space, but that broke generation because the VPS also deletes its temp
        # copy after confirm-download, leaving no copy anywhere.
