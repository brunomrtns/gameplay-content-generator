"""Tests for job state transitions and render plan builder."""

from pathlib import Path

import pytest

from gpcg.application.render_plan_builder import RenderPlanBuilder
from gpcg.domain.game_repository import get_or_create
from gpcg.core.models import (
    ContentPlan,
    Job,
    JobStage,
    JobStatus,
    Script,
    ScriptStatus,
)
from gpcg.domains.games.models import GameplaySource
from gpcg.infrastructure.database import init_db, session_scope


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("GPCG_DB_PATH", str(db_path))
    monkeypatch.setenv("GPCG_DATA_DIR", str(tmp_path))
    from gpcg.config import get_settings
    get_settings.cache_clear()
    from gpcg.infrastructure import database
    database._engine = None
    database._SessionLocal = None
    init_db()
    yield
    get_settings.cache_clear()
    database._engine = None
    database._SessionLocal = None


class TestJobState:
    def test_create_job_queued(self):
        import uuid

        with session_scope() as s:
            game = get_or_create(s, "Bully")
            job = Job(
                job_uuid=str(uuid.uuid4()),
                type="generate_short",
                game_id=game.id,
                status=JobStatus.queued.value,
                stage=JobStage.content_planning.value,
                progress=0.0,
            )
            s.add(job)
            s.flush()
            assert job.id is not None
            assert job.status == JobStatus.queued.value

    def test_job_stage_transitions(self):
        import uuid

        with session_scope() as s:
            game = get_or_create(s, "Bully")
            job = Job(
                job_uuid=str(uuid.uuid4()),
                type="generate_short",
                game_id=game.id,
                status=JobStatus.queued.value,
                stage=JobStage.content_planning.value,
            )
            s.add(job)
            s.flush()
            jid = job.id

        # Transition to running
        with session_scope() as s:
            j = s.get(Job, jid)
            j.status = JobStatus.running.value
            j.stage = JobStage.tts.value

        with session_scope() as s:
            j = s.get(Job, jid)
            assert j.status == JobStatus.running.value
            assert j.stage == JobStage.tts.value

        # Complete
        with session_scope() as s:
            j = s.get(Job, jid)
            j.status = JobStatus.completed.value
            j.stage = JobStage.done.value
            j.progress = 100.0


class TestRenderPlanBuilder:
    def test_build_plan_with_clips(self, sample_video: Path, tmp_path: Path):
        from gpcg.application.gameplay_asset_service import AssetCreate, GameplayAssetService
        from gpcg.application.gameplay_selector import GameplaySelector, SelectedClip

        with session_scope() as s:
            game = get_or_create(s, "Bully")
            src = GameplaySource(
                game_id=game.id,
                file_path=str(sample_video),
                filename="Bully_test.mp4",
                file_hash="hash_render_test",
                duration=3.0,
                width=640,
                height=480,
                ingestion_status="ready",
            )
            s.add(src)
            s.flush()
            asset = GameplayAssetService.create(s, AssetCreate(source_id=src.id, start_sec=0, end_sec=3))
            s.flush()

            plan = ContentPlan(
                game_id=game.id,
                topic="Bully curiosities",
                hook="Did you know?",
                tone="curious",
                energy=0.7,
                music_mood="mysterious",
            )
            s.add(plan)
            s.flush()

            script = Script(
                content_plan_id=plan.id,
                draft="draft text",
                optimized="optimized text",
                final="final narration text here that is long enough",
                status=ScriptStatus.final.value,
                char_count=900,
            )
            s.add(script)
            s.flush()

            # Build a fake narration wav
            import subprocess
            narration = tmp_path / "narration.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(narration)],
                check=True, capture_output=True,
            )

            selector = GameplaySelector()
            clips = selector.select(s, game.id, target_duration=3.0)
            assert len(clips) > 0

            builder = RenderPlanBuilder()
            rp = builder.build(
                s,
                plan,
                script,
                narration_wav=narration,
                narration_duration=3.0,
                subtitle_mapping={"tts_text": script.final, "expansions": []},
                selected_clips=clips,
                music_path=None,
            )

            assert rp.batch_id.startswith("gpcg_")
            assert len(rp.scene_timeline) >= 1
            assert rp.request_data["video_profile"] == "gpcg_9_16"
            assert rp.request_data["audio_principal"] == str(narration)
            assert rp.request_data["img_dir"] == str(rp.scene_dir)
            # Scene files exist
            from pathlib import Path
            scene_files = list(Path(rp.scene_dir).glob("scene_*.mp4"))
            assert len(scene_files) >= 1

            # Cleanup
            rp.cleanup()
            assert not Path(rp.scene_dir).exists()
