"""Game Catalog Service — IGDB-synced game catalog for GPCG.

A standalone FastAPI service that runs as a separate process (gpcg catalog)
on port 8788. It syncs game data from IGDB (the canonical source) and
serves query endpoints for the GPCG API and frontend.

The catalog service is intentionally "dumb": it only syncs and serves data.
All intelligence (game association from gameplay mapping) lives in the
GPCG worker, which uses the local LLM (Ollama) to compare mapping data
against catalog entries.

See docs/CATALOG_SERVICE_PLAN.md for the full architecture.
"""
