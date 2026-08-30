"""Content collection job handler mixin for :class:`RemoteWorker`.

Extracted from ``remote_worker.py``. Contains the RSS content collection
pipeline (collect → score with LLM → sync KnowledgeItems back to VPS).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class ContentCollectMixin:
    """Content collection job processing."""

    def _process_content_collect_job(self, job: dict) -> None:
        """Process a content_collect job: collect RSS → score → sync.

        Runs entirely locally (RSS + Ollama for editorial scoring).
        Syncs collected KnowledgeItems back to VPS via /jobs/{id}/sync-knowledge-items.
        """
        job_id = job["id"]

        self.update_job_status(job_id, status="running", stage="content_collect", progress=0.1)
        self.send_status("busy", "Coletando conteúdo (RSS)", job_id=job_id, activity_key="worker.activity.collecting_rss")

        from gpcg.application.content_collectors import collect_rss_items
        from gpcg.infrastructure.llm import LLMClient

        # Get game names from VPS (games that have gameplay sources)
        game_names = []
        try:
            resp = self.client.get("/api/games")
            if resp.status_code == 200:
                games = resp.json()
                if isinstance(games, list):
                    game_names = [g.get("canonical_name") for g in games if g.get("canonical_name")]
                elif isinstance(games, dict) and "games" in games:
                    game_names = [g.get("canonical_name") for g in games["games"] if g.get("canonical_name")]
        except Exception as e:
            log.warning(f"Could not fetch game list from VPS: {e}")

        # V2: Try to get editorial brief (expanded search queries) from VPS
        # This replaces basic "{game} game" queries with editorial queries
        # like "Bully hidden secrets", "Bully easter egg", "Bully story lore"
        search_queries = None
        user_id = job.get("user_id")
        if user_id:
            try:
                resp = self.client.get(f"/api/automation/editorial-brief/{user_id}")
                if resp.status_code == 200:
                    brief_data = resp.json()
                    search_queries = brief_data.get("search_queries", [])
                    if search_queries:
                        log.info(f"Editorial brief: {len(search_queries)} expanded queries "
                                 f"(templates={brief_data.get('active_templates', [])})")
                    else:
                        log.info("Editorial brief empty — falling back to basic game queries")
            except Exception as e:
                log.warning(f"Could not fetch editorial brief: {e}")

        log.info(f"Collecting RSS for games: {game_names} (editorial_queries={len(search_queries) if search_queries else 0})")

        # Collect RSS feeds locally (headless, no DB)
        try:
            items = collect_rss_items(
                game_names=game_names if game_names else None,
                search_queries=search_queries if search_queries else None,
            )
            log.info(f"Collected {len(items)} items from RSS feeds")
        except Exception as e:
            log.exception(f"RSS collection failed: {e}")
            self._sync_knowledge_items(job_id, error=str(e))
            self.submit_job_result(job_id, status="failed", error=str(e))
            return

        if not items:
            log.info("No items collected from RSS")
            self._sync_knowledge_items(job_id, items=[], cleaned_count=0)
            self.submit_job_result(job_id, status="completed", artifacts={"collected": 0})
            return

        # Score items with local LLM (5 editorial dimensions)
        self.update_job_status(job_id, status="running", stage="scoring", progress=0.3)
        from gpcg.application.knowledge_item_service import score_rss_item_headless
        try:
            llm = LLMClient()
        except Exception as e:
            log.warning(f"LLM init failed for scoring (using heuristic): {e}")
            llm = None

        scored_items = []
        rejected_count = 0
        for i, item in enumerate(items):
            score, rejection_reason = score_rss_item_headless(
                title=item.title,
                content=item.content,
                item_type=item.item_type,
                source_type=item.source_name or item.source_type,
                llm=llm,
            )
            item.editorial_score = score
            if rejection_reason:
                # Skip rejected items (clickbait/promotion/rumor) — don't sync to VPS
                rejected_count += 1
                if (i + 1) % 10 == 0:
                    progress = 0.3 + (i + 1) / len(items) * 0.5
                    self.update_job_status(job_id, status="running", stage="scoring", progress=progress)
                continue

            scored_items.append(item)

            if (i + 1) % 10 == 0 or i + 1 == len(items):
                progress = 0.3 + (i + 1) / len(items) * 0.5
                self.update_job_status(job_id, status="running", stage="scoring", progress=progress)

        if rejected_count > 0:
            log.info(f"Content collection: {rejected_count} items rejected by quality gate (not synced)")

        # Sync items back to VPS (VPS handles cleanup of old news)
        self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
        sync_items = []
        for item in scored_items:
            sync_items.append({
                "title": item.title,
                "content": item.content,
                "item_type": item.item_type,
                "source_type": item.source_type,
                "source_url": item.source_url,
                "source_name": item.source_name,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "editorial_score": item.editorial_score,
                "franchise": item.franchise,
                "developer": item.developer,
                "game_id": item.game_id,
                "content_hash": item.content_hash,
                "tags": item.tags,
            })

        sync_resp = self._sync_knowledge_items(job_id, items=sync_items, cleaned_count=0)
        log.info(f"Content collection synced to VPS: {sync_resp}")

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "collected": len(scored_items),
            "synced": sync_resp.get("inserted", 0),
            "skipped": sync_resp.get("skipped", 0),
        })
        log.info(f"Content collection job #{job_id} completed: {len(scored_items)} items")

    def _sync_knowledge_items(self, job_id: int, items: list = None, cleaned_count: int = 0, error: str = None) -> dict:
        """Send collected KnowledgeItems to VPS."""
        payload = {
            "items": items or [],
            "cleaned_count": cleaned_count,
        }
        if error:
            payload["error"] = error
        resp = self.client.post(f"/api/jobs/{job_id}/sync-knowledge-items", json=payload)
        resp.raise_for_status()
        return resp.json()
