"""Game enrichment job handler mixin for :class:`RemoteWorker`.

Extracted from ``remote_worker.py``. Contains the game enrichment pipeline
(Wikidata + Wikipedia + LLM lore generation) and the sync helper.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class EnrichmentMixin:
    """Game enrichment job processing."""

    def _process_game_enrich_job(self, job: dict) -> None:
        """Process a game_enrich job: fetch Wikidata/Wikipedia → generate lore → sync.

        Runs entirely locally (Wikidata + Wikipedia + Ollama for lore).
        Syncs the enriched Game data back to VPS via /jobs/{id}/sync-enrichment.
        """
        job_id = job["id"]
        game_id = job.get("game_id")
        if not game_id:
            self.submit_job_result(job_id, status="failed", error="No game_id in job")
            return

        self.update_job_status(job_id, status="running", stage="enrichment", progress=0.1)
        self.send_status("busy", f"Enriquecendo jogo #{game_id}", job_id=job_id, activity_key="worker.activity.enriching_game")

        from gpcg.application.game_enrichment import fetch_enrichment_data
        from gpcg.infrastructure.llm import LLMClient

        # Get game name from job data or fetch from VPS API
        game_name = job.get("game", {}).get("canonical_name", "")
        if not game_name:
            try:
                resp = self.client.get(f"/api/games/{game_id}")
                if resp.status_code == 200:
                    game_name = resp.json().get("canonical_name", "")
            except Exception:
                pass

        if not game_name:
            self.submit_job_result(job_id, status="failed", error="Could not determine game name")
            return

        log.info(f"Enriching game '{game_name}' (id={game_id})")

        # Run enrichment locally (Wikidata + Wikipedia + LLM lore) — headless, no DB
        try:
            llm = LLMClient()
            result = fetch_enrichment_data(game_name, llm=llm)
        except Exception as e:
            log.exception(f"Enrichment failed for '{game_name}': {e}")
            self._sync_enrichment(job_id, enrichment_error=str(e))
            self.submit_job_result(job_id, status="failed", error=str(e))
            return

        if not result.success:
            log.warning(f"Enrichment failed for '{game_name}': {result.error}")
            self._sync_enrichment(job_id, enrichment_error=result.error)
            self.submit_job_result(job_id, status="failed", error=result.error)
            return

        # Sync enriched data back to VPS
        self.update_job_status(job_id, status="running", stage="sync", progress=0.9)
        sync_data = {
            "description": result.description,
            "developer": result.developer,
            "publisher": result.publisher,
            "franchise": result.franchise,
            "genres": result.genres or [],
            "themes": result.themes or [],
            "lore_summary": result.lore_summary,
            "release_date": result.release_date.isoformat() if result.release_date else None,
            "external_ids": result.external_ids or {},
            "aliases": result.aliases or [],
        }
        sync_resp = self._sync_enrichment(job_id, **sync_data)
        log.info(f"Enrichment synced to VPS for '{game_name}': {sync_resp}")

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "enriched": True,
            "game_id": game_id,
            "developer": result.developer,
            "franchise": result.franchise,
        })
        log.info(f"Game enrichment job #{job_id} completed for '{game_name}'")

    def _sync_enrichment(self, job_id: int, enrichment_error: str = None, **kwargs) -> dict:
        """Send enrichment results to VPS."""
        payload = {"enrichment_error": enrichment_error} if enrichment_error else kwargs
        resp = self.client.post(f"/api/jobs/{job_id}/sync-enrichment", json=payload)
        resp.raise_for_status()
        return resp.json()
