"""Domain-aware Automation Strategies.

Dispatches automation behavior based on the user's channel domain:
- Games: existing behavior (GameplaySource, KnowledgeItem, idea_queue)
- Kids: new behavior (KidsIdea, StoryAsset, kids_idea_queue)

The Games strategy is a thin wrapper that preserves the existing code path
100% unchanged. The Kids strategy implements the new Kids automation path.

Both strategies share the same interface:
- `check(auto, db)` → dict | None  (should a job be created?)
- `create_job(user_id)` → int | None  (create the job, return job_id)
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from gpcg.core.models import (
    Automation,
    ChannelProfile,
    ContentDomain,
    Job,
    JobStatus,
    JobType,
    User,
)
from gpcg.logging import get_logger

log = get_logger(__name__)


def get_user_domain(db: Session, user_id: int) -> str:
    """Get the user's channel domain. Defaults to games."""
    profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == user_id
    ).first()
    if profile and profile.domain:
        return profile.domain
    return ContentDomain.games.value


# ── Games Strategy ───────────────────────────────────────────────────────────


class GamesAutomationStrategy:
    """Games automation strategy — preserves existing behavior.

    This is a thin wrapper. The actual Games logic lives in
    `automation_routes.check_automation` and `create_job_from_automation`.
    This class exists only for interface symmetry with KidsAutomationStrategy.
    """

    @staticmethod
    def check(auto: Automation, db: Session) -> Optional[dict]:
        """Check if a Games automation needs a job.

        Returns the pending dict if a job should be created, None otherwise.
        The actual logic is in check_automation() — this is just a marker
        that the Games path should be used.
        """
        return None  # handled by existing check_automation code

    @staticmethod
    def create_job(user_id: int) -> Optional[int]:
        """Create a Games generation job.

        Delegates to the existing create_job_from_automation() function.
        """
        from gpcg.api.automation_routes import create_job_from_automation
        return create_job_from_automation(user_id)


# ── Kids Strategy ────────────────────────────────────────────────────────────


class KidsAutomationStrategy:
    """Kids automation strategy.

    Kids automation flow:
    1. Check if user has YouTube connected
    2. Check if there are StoryAssets ready (visual material)
    3. Reconcile the Kids idea queue (clean + auto-fill)
    4. If queue has ideas, return pending so the worker can create a job
    5. Worker calls create_job, which:
       a. Picks the first idea from kids_idea_queue
       b. Converts it to a KidsTopic (if not already converted)
       c. Creates a generate_short job with domain=kids
    """

    @staticmethod
    def check(auto: Automation, db: Session) -> Optional[dict]:
        """Check if a Kids automation needs a job.

        Returns the pending dict if a job should be created, None otherwise.
        """
        from gpcg.domains.kids.models import StoryAsset, AssetProcessingStatus
        from gpcg.domains.kids.idea_service import (
            clean_kids_queue,
            reconcile_kids_queue,
        )

        user = db.get(User, auto.user_id)
        if not user or not user.is_active:
            return None

        # Check YouTube connection
        if not user.google_user_id:
            return None

        # Check if there are StoryAssets ready (visual material for Kids)
        ready_assets = db.query(StoryAsset).filter(
            StoryAsset.user_id == auto.user_id,
            StoryAsset.processing_status == AssetProcessingStatus.ready.value,
        ).count()
        if ready_assets == 0:
            return None

        # Check if there's already an active generation job for this user
        active = db.query(Job).filter(
            Job.user_id == auto.user_id,
            Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
            Job.type.in_([
                JobType.generate_short.value,
                JobType.curiosity_short.value,
            ]),
        ).first()
        if active:
            return None

        # Reconcile Kids idea queue (clean + auto-fill)
        clean_kids_queue(db, auto.user_id)
        reconcile_kids_queue(db, auto.user_id)
        db.commit()
        db.refresh(auto)

        cfg = auto.config or {}
        kids_queue = cfg.get("kids_idea_queue", [])
        if not kids_queue:
            return None

        return {
            "user_id": auto.user_id,
            "automation_id": auto.id,
            "config": cfg,
            "kids_idea_queue": kids_queue,
            "domain": ContentDomain.kids.value,
            "queue_mode": cfg.get("kids_queue_mode", "automatic"),
        }

    @staticmethod
    def create_job(user_id: int) -> Optional[int]:
        """Create a Kids generation job from the idea queue.

        Picks the first idea from kids_idea_queue, converts it to a KidsTopic
        (if not already converted), and creates a generate_short job.
        """
        import uuid
        from gpcg.infrastructure.database import session_scope
        from gpcg.domains.kids.models import KidsTopic, StoryAsset
        from gpcg.domains.kids.idea_service import (
            convert_to_topic,
            get_by_id as get_idea_by_id,
            is_terminal,
        )
        from gpcg.domains.kids.models import KidsIdeaStatus

        with session_scope() as session:
            user = session.get(User, user_id)
            if not user or not user.is_active:
                return None

            auto = session.query(Automation).filter(
                Automation.user_id == user_id
            ).first()
            if not auto or auto.status != "running":
                return None

            if not user.google_user_id:
                return None

            # Check for active generation jobs
            active_gen = session.query(Job).filter(
                Job.user_id == user_id,
                Job.status.in_([JobStatus.queued.value, JobStatus.running.value]),
                Job.type != "content_collect",
            ).count()
            if active_gen > 0:
                return None

            # Check for ready StoryAssets
            from gpcg.domains.kids.models import AssetProcessingStatus
            ready_assets = session.query(StoryAsset).filter(
                StoryAsset.user_id == user_id,
                StoryAsset.processing_status == AssetProcessingStatus.ready.value,
            ).count()
            if ready_assets == 0:
                return None

            # Get the Kids idea queue
            cfg = dict(auto.config or {})
            kids_queue = cfg.get("kids_idea_queue", [])
            if not kids_queue:
                return None

            # Find the first valid idea in the queue
            idea = None
            queue_index = 0
            for i, idea_id in enumerate(kids_queue):
                candidate = get_idea_by_id(session, int(idea_id))
                if candidate and candidate.user_id == user_id:
                    if not is_terminal(candidate.status):
                        idea = candidate
                        queue_index = i
                        break

            if not idea:
                log.info(f"kids_automation: no valid idea in queue for user {user_id}")
                return None

            # Convert idea to topic if not already converted
            topic_id = idea.topic_id
            if not topic_id:
                topic = convert_to_topic(session, idea.id)
                if not topic:
                    log.warning(f"kids_automation: failed to convert idea #{idea.id} to topic")
                    return None
                topic_id = topic.id

            # Get the topic
            topic = session.get(KidsTopic, topic_id)
            if not topic:
                log.warning(f"kids_automation: topic #{topic_id} not found")
                return None

            # Create the generation job
            job = Job(
                job_uuid=str(uuid.uuid4()),
                type=JobType.generate_short.value,
                status=JobStatus.queued.value,
                stage="queued",
                domain=ContentDomain.kids.value,
                priority="normal",
                user_id=user_id,
                artifacts={
                    "topic_id": topic.id,
                    "topic_title": topic.title,
                    "idea_id": idea.id,
                    "source": "kids_idea_queue",
                },
            )
            session.add(job)
            session.flush()
            job_id = job.id

            # Remove the idea from the queue
            new_queue = [
                iid for idx, iid in enumerate(kids_queue)
                if idx != queue_index
            ]
            cfg["kids_idea_queue"] = new_queue
            auto.config = cfg
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(auto, "config")

            session.commit()
            log.info(
                f"kids_automation: created job #{job_id} for user {user_id} "
                f"from idea #{idea.id} → topic #{topic.id}"
            )
            return job_id


# ── Dispatch ─────────────────────────────────────────────────────────────────


def get_strategy(domain: str):
    """Get the automation strategy for a domain."""
    if domain == ContentDomain.kids.value:
        return KidsAutomationStrategy
    return GamesAutomationStrategy
