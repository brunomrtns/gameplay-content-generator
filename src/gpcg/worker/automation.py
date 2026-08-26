"""Automation mixin for :class:`RemoteWorker`.

Extracted from ``remote_worker.py``. Contains the automation check loop
(editorial decisions, idea queue consumption) and the auto content
collection scheduler that run inside the worker's main polling loop.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class AutomationMixin:
    """Automation scheduling and editorial decision helpers."""

    def _maybe_auto_collect(self) -> None:
        """Auto-trigger content collection every N hours if no collection job is active.

        Uses gpcg_content_collection_interval_hours from config (default: 6h).
        Creates a content_collect job on the VPS via the worker-auth endpoint.
        The VPS deduplicates (blocks if a content_collect job is already queued/running).
        """
        import time as _time
        now = _time.time()
        if now - self._last_collection_time < self._collection_interval_sec:
            return

        # Check if there's already a content_collect job queued/running on VPS
        try:
            resp = self.client.get("/api/jobs?type=content_collect&status=queued,running&limit=1")
            if resp.status_code == 200:
                data = resp.json()
                jobs = data if isinstance(data, list) else data.get("jobs", [])
                if jobs:
                    # Already collecting — wait
                    self._last_collection_time = now  # reset timer to avoid spamming
                    return
        except Exception:
            pass  # non-fatal — try to create the job anyway

        # Trigger content collection via worker-auth endpoint
        try:
            resp = self.client.post("/api/automation/trigger-content-collection")
            if resp.status_code == 200:
                log.info(f"Auto content collection triggered (interval={self._collection_interval_sec/3600:.0f}h)")
                self._last_collection_time = now
            elif resp.status_code == 409:
                # Already queued — reset timer
                self._last_collection_time = now
            else:
                log.warning(f"Auto content collection failed: {resp.status_code} {resp.text}")
        except Exception as e:
            log.warning(f"Auto content collection error: {e}")

    def _check_automations(self) -> None:
        """Check if any running automation needs a new job created.

        V2 flow:
        1. POST /api/automation/check — VPS returns pending automations
           (running, no active job, has gameplays, YouTube connected)
        2. For each pending automation, GET /api/automation/editorial-data/{user_id}
           — VPS returns inventory + history + channel profile
        3. Run EditorialStrategyService locally (with LLM/Ollama)
        4. POST /api/automation/create-job — VPS creates the job with
           automation config (subtitle/transition/voice settings)
        """
        try:
            resp = self.client.post("/api/automation/check")
            if resp.status_code != 200:
                return
            data = resp.json()
            pending = data.get("pending", [])
            if not pending:
                return

            for item in pending:
                user_id = item["user_id"]
                idea_queue = item.get("idea_queue", [])
                queue_mode = item.get("queue_mode", "automatic")
                if idea_queue:
                    # User has curated ideas in queue — consume directly (no LLM needed)
                    self._consume_idea_queue(user_id)
                elif queue_mode == "manual":
                    # V3: Manual mode — do NOT auto-generate when queue is empty.
                    # The user explicitly wants to curate every video.
                    log.info(f"User {user_id}: queue empty + manual mode — skipping")
                else:
                    # Automatic mode — fall back to editorial decision
                    self._make_editorial_decision(user_id)
        except Exception as e:
            log.warning(f"Automation check failed: {e}")

    def _consume_idea_queue(self, user_id: int) -> None:
        """Create a job from the user's idea queue (no LLM editorial decision needed)."""
        try:
            resp = self.client.post("/api/automation/consume-queue", json={"user_id": user_id})
            if resp.status_code == 200:
                job_data = resp.json()
                log.info(f"Idea queue: created job #{job_data.get('job_id')} for user {user_id}")
            elif resp.status_code == 409:
                # Queue empty or active job exists — not an error
                pass
            else:
                log.warning(f"Consume idea queue failed for user {user_id}: {resp.status_code} {resp.text}")
        except Exception as e:
            log.warning(f"Consume idea queue for user {user_id} failed: {e}")

    def _make_editorial_decision(self, user_id: int) -> None:
        """Fetch editorial data from VPS, decide locally with LLM, create job."""
        try:
            # 1. Fetch editorial data from VPS
            resp = self.client.get(f"/api/automation/editorial-data/{user_id}")
            if resp.status_code != 200:
                log.warning(f"Failed to fetch editorial data for user {user_id}: {resp.status_code}")
                return
            data = resp.json()
            inventory = data.get("inventory", [])
            history = data.get("history", {})
            channel_context = data.get("channel_context", "")
            general_ideas = data.get("general_ideas", [])

            if not inventory:
                log.info(f"No games in inventory for user {user_id}")
                return

            # 2. Run editorial decision locally (with LLM)
            from gpcg.infrastructure.llm import LLMClient
            from gpcg.application.editorial_strategy import (
                EditorialStrategyService,
                EditorialDecision,
                GameInventory,
            )

            llm = LLMClient()
            editorial = EditorialStrategyService(llm=llm)

            # Reconstruct GameInventory objects from the API data
            inventories = []
            for inv_data in inventory:
                inv = GameInventory(
                    game_id=inv_data["game_id"],
                    game_name=inv_data["game_name"],
                )
                inv.gameplay_sources_ready = inv_data.get("gameplay_sources_ready", 0)
                inv.gameplay_clips_available = inv_data.get("gameplay_clips_available", 0)
                inv.total_gameplay_duration = inv_data.get("total_gameplay_duration", 0.0)
                inv.gameplay_sources_total = inv_data.get("gameplay_sources_total", 0)
                inv.facts_available = inv_data.get("facts_available", 0)
                inv.facts_unused = inv_data.get("facts_unused", 0)
                inv.knowledge_chunks = inv_data.get("knowledge_chunks", 0)
                inv.knowledge_items = inv_data.get("knowledge_items", 0)
                inv.videos_produced = inv_data.get("videos_produced", 0)
                inv.recent_topics = inv_data.get("recent_topics", [])
                inventories.append(inv)

            # Use LLM to decide (or heuristic fallback)
            # V2: Pass ALL inventories (not just producible) + general_ideas
            # so the LLM can also choose curiosity_short with a general idea.
            # The LLM prompt explains both options clearly.
            try:
                decision = editorial._llm_decision_from_data(
                    inventories, history, channel_context,
                    general_ideas=general_ideas,
                )
            except Exception as e:
                log.warning(f"LLM editorial decision failed: {e}, using heuristic")
                decision = editorial._heuristic_decision(inventories, history)

            if not decision.success:
                log.info(f"Editorial decision not successful: {decision.error}")
                return

            # Pick a fact or knowledge_item for the chosen game
            if decision.game_id:
                chosen_inv = next((g for g in inventory if g["game_id"] == decision.game_id), None)
                if chosen_inv:
                    # V2: Prefer KnowledgeItems if available (content ideas)
                    ki_list = chosen_inv.get("knowledge_items_list", [])
                    if ki_list:
                        decision.fact_id = None  # KI will be picked by ContentPlanningService
                    elif chosen_inv.get("facts"):
                        recent_fact_ids = set(history.get("recent_fact_ids", []))
                        fresh_facts = [f for f in chosen_inv["facts"] if f["id"] not in recent_fact_ids]
                        if fresh_facts:
                            decision.fact_id = fresh_facts[0]["id"]
                        elif chosen_inv["facts"]:
                            decision.fact_id = chosen_inv["facts"][0]["id"]

            log.info(
                f"Editorial decision for user {user_id}: "
                f"type={decision.job_type} game_id={decision.game_id} "
                f"bg_game_id={decision.background_game_id} "
                f"fact_id={decision.fact_id} reason={decision.reason[:80]}"
            )

            # 3. Create the job on VPS via API
            create_resp = self.client.post("/api/automation/create-job", json={
                "user_id": user_id,
                "game_id": decision.game_id,
                "fact_id": decision.fact_id,
                "job_type": decision.job_type,
                "background_game_id": decision.background_game_id,
                "topic_hint": decision.topic_hint,
                "reason": decision.reason,
            })
            if create_resp.status_code == 200:
                job_data = create_resp.json()
                log.info(f"Automation created job #{job_data.get('job_id')} for user {user_id}")
            else:
                log.warning(f"Failed to create job from decision: {create_resp.status_code} {create_resp.text}")
        except Exception as e:
            log.warning(f"Editorial decision for user {user_id} failed: {e}")
