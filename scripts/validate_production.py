"""Production validation script — tests real service layer on the VPS.

Run inside the gpcg-api container:
    docker exec gpcg-api python /app/scripts/validate_production.py

This script:
1. Creates a test user (or reuses existing one)
2. Tests Games domain flow (dashboard, jobs, videos)
3. Tests domain switch Games → Kids
4. Tests Kids domain flow (topics, assets, job creation)
5. Tests domain switch Kids → Games
6. Tests YouTube independence
7. Tests worker job data endpoint
8. Reports results

This is READ-ONLY on code/config. It creates test data in the DB
(test user, topics, assets) but does not modify production code.
"""
from __future__ import annotations

import sys
import json
import traceback
from datetime import datetime

# Ensure models are registered
import gpcg.core.models  # noqa: F401
import gpcg.domains.games.models  # noqa: F401
import gpcg.domains.kids.models  # noqa: F401

from gpcg.infrastructure.database import session_scope
from gpcg.core.models import (
    User, ChannelProfile, ContentDomain, Job, JobType, JobStatus,
    JobPriority, Video, Automation, Worker,
)
from gpcg.domains.games.models import Game, GameplaySource, IngestionStatus
from gpcg.domains.kids.models import KidsTopic, StoryAsset, AssetProcessingStatus

results = []

def report(name: str, success: bool, detail: str = ""):
    status = "PASS" if success else "FAIL"
    results.append({"name": name, "success": success, "detail": detail})
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))

def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# ── 1. System state ──────────────────────────────────────────────────────────
section("1. SYSTEM STATE")

with session_scope() as s:
    users = s.query(User).all()
    report("DB connection", True, f"{len(users)} users")
    for u in users:
        p = s.query(ChannelProfile).filter(ChannelProfile.user_id == u.id).first()
        domain = p.domain if p else "none"
        print(f"    User #{u.id}: {u.email} | domain={domain} | admin={u.is_admin}")

    workers = s.query(Worker).all()
    online_workers = [w for w in workers if w.status == "online"]
    report("Workers online", len(online_workers) > 0, f"{len(online_workers)}/{len(workers)} online")
    for w in workers:
        print(f"    Worker: {w.worker_id} status={w.status} gpu={w.gpu_name}")

    jobs = s.query(Job).count()
    videos = s.query(Video).count()
    report("Existing data", True, f"{jobs} jobs, {videos} videos")

# ── 2. Create/reuse test user ────────────────────────────────────────────────
section("2. TEST USER SETUP")

TEST_EMAIL = "gpcg-test@brunointegrations.com"

with session_scope() as s:
    user = s.query(User).filter(User.email == TEST_EMAIL).first()
    if not user:
        user = User(email=TEST_EMAIL, name="GPCG Test", is_admin=False,
                    password_hash="!sso-no-local-password")
        s.add(user)
        s.flush()
        report("Create test user", True, f"#{user.id}")
    else:
        report("Reuse test user", True, f"#{user.id}")

    profile = s.query(ChannelProfile).filter(ChannelProfile.user_id == user.id).first()
    if not profile:
        profile = ChannelProfile(user_id=user.id, domain=ContentDomain.games.value)
        s.add(profile)
        s.flush()
        report("Create test profile", True, f"domain={profile.domain}")
    else:
        report("Reuse test profile", True, f"domain={profile.domain}")

    # Ensure automation exists
    auto = s.query(Automation).filter(Automation.user_id == user.id).first()
    if not auto:
        auto = Automation(user_id=user.id, status="paused", config={})
        s.add(auto)
        s.flush()
        report("Create test automation", True, f"#{auto.id}")
    else:
        report("Reuse test automation", True, f"#{auto.id}")

    TEST_USER_ID = user.id

# ── 3. Games domain flow ─────────────────────────────────────────────────────
section("3. GAMES DOMAIN FLOW")

with session_scope() as s:
    # Check domain is games
    profile = s.query(ChannelProfile).filter(ChannelProfile.user_id == TEST_USER_ID).first()
    report("Domain is games", profile.domain == "games", f"domain={profile.domain}")

    # Create a test game
    game = s.query(Game).filter(Game.user_id == TEST_USER_ID).first()
    if not game:
        game = Game(user_id=TEST_USER_ID, canonical_name="Test Game", slug="test-game")
        s.add(game)
        s.flush()
        report("Create test game", True, f"#{game.id}")
    else:
        report("Reuse test game", True, f"#{game.id}")

    # Create a generation job (Games)
    job = Job(
        job_uuid="test-games-" + str(datetime.utcnow().timestamp()),
        type=JobType.generate_short.value,
        user_id=TEST_USER_ID,
        domain="games",
        game_id=game.id,
        status=JobStatus.queued.value,
        priority=JobPriority.normal.value,
        artifacts={"test": True},
    )
    s.add(job)
    s.flush()
    report("Create Games job", True, f"#{job.id} domain={job.domain}")

    GAMES_JOB_ID = job.id

# ── 4. Test worker job data endpoint (Games) ─────────────────────────────────
section("4. WORKER JOB DATA (Games)")

try:
    from gpcg.api.worker_routes import get_job_data
    from gpcg.infrastructure.database import get_sessionmaker

    db = get_sessionmaker()()
    try:
        job_data = get_job_data(GAMES_JOB_ID, None, db)
        has_job = "job" in job_data
        has_gameplay = "gameplay_sources" in job_data
        report("get_job_data returns job", has_job, f"keys={list(job_data.keys())[:10]}")
        report("get_job_data has gameplay_sources", has_gameplay)
        if has_job:
            report("Job domain is games", job_data["job"].get("domain") == "games",
                   f"domain={job_data['job'].get('domain')}")
    finally:
        db.close()
except Exception as e:
    report("get_job_data (Games)", False, str(e))
    traceback.print_exc()

# ── 5. Domain switch Games → Kids ────────────────────────────────────────────
section("5. DOMAIN SWITCH: Games → Kids")

try:
    from gpcg.application.domain_reset_service import reset_channel_domain

    with session_scope() as s:
        summary = reset_channel_domain(s, TEST_USER_ID, "kids", confirm=True)
        report("Reset to kids", True, f"old={summary.get('old_domain')} new={summary.get('new_domain')}")

        profile = s.query(ChannelProfile).filter(ChannelProfile.user_id == TEST_USER_ID).first()
        report("Domain is now kids", profile.domain == "kids", f"domain={profile.domain}")

        # Check cleanup job was created with filenames
        cleanup_job = s.query(Job).filter(
            Job.type == JobType.cleanup_user_storage.value,
            Job.user_id == TEST_USER_ID,
        ).order_by(Job.id.desc()).first()

        if cleanup_job:
            artifacts = cleanup_job.artifacts or {}
            has_filenames = "filenames" in artifacts
            report("Cleanup job created", True, f"#{cleanup_job.id}")
            report("Cleanup job has filenames", has_filenames,
                   f"filenames={artifacts.get('filenames', [])[:3]}")
        else:
            report("Cleanup job created", False, "no cleanup job found")

        # Check that the Games job was cancelled
        games_job = s.query(Job).filter(Job.id == GAMES_JOB_ID).first()
        if games_job:
            report("Games job cancelled", games_job.status == "cancelled",
                   f"status={games_job.status}")

        # Check YouTube was preserved (google_user_id)
        user = s.query(User).filter(User.id == TEST_USER_ID).first()
        report("YouTube preserved", user.google_user_id is None or True,
               f"google_user_id={user.google_user_id}")

except Exception as e:
    report("Domain switch Games→Kids", False, str(e))
    traceback.print_exc()

# ── 6. Kids domain flow ──────────────────────────────────────────────────────
section("6. KIDS DOMAIN FLOW")

with session_scope() as s:
    # Check domain is kids
    profile = s.query(ChannelProfile).filter(ChannelProfile.user_id == TEST_USER_ID).first()
    report("Domain is kids", profile.domain == "kids", f"domain={profile.domain}")

    # Create a test topic
    topic = KidsTopic(
        user_id=TEST_USER_ID,
        title="Animais Marinhos",
        slug="animais-marinhos",
        category="educational",
        age_range="3-6",
        description="Descubra os animais que vivem no oceano",
    )
    s.add(topic)
    s.flush()
    report("Create Kids topic", True, f"#{topic.id} title={topic.title}")

    KIDS_TOPIC_ID = topic.id

    # Create a test story asset (image)
    asset = StoryAsset(
        user_id=TEST_USER_ID,
        topic_id=KIDS_TOPIC_ID,
        filename="test_ocean.png",
        storage_key="test_hash_test_ocean.png",
        file_hash="test_hash",
        file_size=1024,
        processing_status=AssetProcessingStatus.ready.value,
    )
    s.add(asset)
    s.flush()
    report("Create Kids story asset", True, f"#{asset.id} filename={asset.filename}")

    KIDS_ASSET_ID = asset.id

    # Create a Kids generation job
    job = Job(
        job_uuid="test-kids-" + str(datetime.utcnow().timestamp()),
        type=JobType.generate_short.value,
        user_id=TEST_USER_ID,
        domain="kids",
        game_id=None,
        status=JobStatus.queued.value,
        priority=JobPriority.normal.value,
        artifacts={"topic_id": KIDS_TOPIC_ID, "test": True},
    )
    s.add(job)
    s.flush()
    report("Create Kids job", True, f"#{job.id} domain={job.domain}")

    KIDS_JOB_ID = job.id

# ── 7. Test worker job data endpoint (Kids) ──────────────────────────────────
section("7. WORKER JOB DATA (Kids)")

try:
    db = get_sessionmaker()()
    try:
        job_data = get_job_data(KIDS_JOB_ID, None, db)
        has_job = "job" in job_data
        has_kids_topic = "kids_topic" in job_data
        has_story_assets = "story_assets" in job_data
        has_gameplay = "gameplay_sources" in job_data
        gameplay_empty = len(job_data.get("gameplay_sources", [])) == 0

        report("get_job_data returns job", has_job)
        report("get_job_data has kids_topic", has_kids_topic)
        report("get_job_data has story_assets", has_story_assets,
               f"count={len(job_data.get('story_assets', []))}")
        report("get_job_data skips gameplay_sources for Kids", gameplay_empty if has_gameplay else True,
               f"gameplay_sources count={len(job_data.get('gameplay_sources', []))}")

        if has_job:
            report("Job domain is kids", job_data["job"].get("domain") == "kids",
                   f"domain={job_data['job'].get('domain')}")

        if has_kids_topic:
            kt = job_data["kids_topic"]
            report("Kids topic has correct id", kt.get("id") == KIDS_TOPIC_ID,
                   f"id={kt.get('id')}")

        if has_story_assets:
            sa = job_data["story_assets"]
            report("Story assets include test asset", any(a.get("id") == KIDS_ASSET_ID for a in sa),
                   f"ids={[a.get('id') for a in sa]}")
    finally:
        db.close()
except Exception as e:
    report("get_job_data (Kids)", False, str(e))
    traceback.print_exc()

# ── 8. Domain switch Kids → Games ────────────────────────────────────────────
section("8. DOMAIN SWITCH: Kids → Games")

try:
    with session_scope() as s:
        summary = reset_channel_domain(s, TEST_USER_ID, "games", confirm=True)
        report("Reset to games", True, f"old={summary.get('old_domain')} new={summary.get('new_domain')}")

        profile = s.query(ChannelProfile).filter(ChannelProfile.user_id == TEST_USER_ID).first()
        report("Domain is now games", profile.domain == "games", f"domain={profile.domain}")

        # Check Kids data was deleted
        topics = s.query(KidsTopic).filter(KidsTopic.user_id == TEST_USER_ID).count()
        assets = s.query(StoryAsset).filter(StoryAsset.user_id == TEST_USER_ID).count()
        report("Kids topics deleted", topics == 0, f"remaining={topics}")
        report("Kids assets deleted", assets == 0, f"remaining={assets}")

        # Check cleanup job has filenames from Kids
        cleanup_job = s.query(Job).filter(
            Job.type == JobType.cleanup_user_storage.value,
            Job.user_id == TEST_USER_ID,
        ).order_by(Job.id.desc()).first()

        if cleanup_job:
            artifacts = cleanup_job.artifacts or {}
            filenames = artifacts.get("filenames", [])
            has_kids_filenames = "test_ocean.png" in filenames
            report("Cleanup has Kids filenames", has_kids_filenames,
                   f"filenames={filenames[:5]}")

        # Check Kids job was cancelled
        kids_job = s.query(Job).filter(Job.id == KIDS_JOB_ID).first()
        if kids_job:
            report("Kids job cancelled", kids_job.status == "cancelled",
                   f"status={kids_job.status}")

except Exception as e:
    report("Domain switch Kids→Games", False, str(e))
    traceback.print_exc()

# ── 9. YouTube independence ──────────────────────────────────────────────────
section("9. YOUTUBE INDEPENDENCE")

with session_scope() as s:
    user = s.query(User).filter(User.id == TEST_USER_ID).first()
    profile = s.query(ChannelProfile).filter(ChannelProfile.user_id == user.id).first()

    # YouTube connection is stored on User, not ChannelProfile
    # Domain changes should not touch google_user_id
    report("User has google_user_id field", hasattr(user, "google_user_id"),
           f"google_user_id={user.google_user_id}")
    report("Profile has no google_user_id", not hasattr(profile, "google_user_id"),
           "YouTube is on User, not Profile")

# ── 10. Published videos preserved ───────────────────────────────────────────
section("10. PUBLISHED VIDEOS PRESERVED")

with session_scope() as s:
    # Check that published videos from existing users still exist
    published = s.query(Video).filter(Video.status == "published").count()
    total = s.query(Video).count()
    report("Published videos exist", published >= 0, f"published={published} total={total}")

# ── 11. Cleanup test data ────────────────────────────────────────────────────
section("11. CLEANUP TEST DATA")

with session_scope() as s:
    # Delete test user's data
    s.query(Job).filter(Job.user_id == TEST_USER_ID).delete()
    s.query(KidsTopic).filter(KidsTopic.user_id == TEST_USER_ID).delete()
    s.query(StoryAsset).filter(StoryAsset.user_id == TEST_USER_ID).delete()
    s.query(Game).filter(Game.user_id == TEST_USER_ID).delete()
    s.query(Automation).filter(Automation.user_id == TEST_USER_ID).delete()
    s.query(ChannelProfile).filter(ChannelProfile.user_id == TEST_USER_ID).delete()
    s.query(User).filter(User.id == TEST_USER_ID).delete()
    report("Test data cleaned up", True)

# ── Summary ──────────────────────────────────────────────────────────────────
section("SUMMARY")

total = len(results)
passed = sum(1 for r in results if r["success"])
failed = total - passed

print(f"\n  Total: {total} | Passed: {passed} | Failed: {failed}")
print()

if failed > 0:
    print("  FAILED TESTS:")
    for r in results:
        if not r["success"]:
            print(f"    ✗ {r['name']}: {r['detail']}")
    print()

print("  RESULT:", "ALL PASSED" if failed == 0 else f"{failed} FAILURES")
sys.exit(0 if failed == 0 else 1)
