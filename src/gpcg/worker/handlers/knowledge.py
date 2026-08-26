"""Knowledge indexing job handler mixin for :class:`RemoteWorker`.

Extracted from ``remote_worker.py``. Contains the document indexing pipeline
(download → parse → chunk → embed → sync chunks back to VPS).
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


class KnowledgeMixin:
    """Knowledge document indexing job processing."""

    def submit_indexing_result(
        self,
        doc_id: int,
        chunks: list[dict],
        error: str = "",
    ) -> dict:
        """Send indexed knowledge chunks back to VPS."""
        resp = self.client.post(
            f"/api/documents/{doc_id}/indexing-result",
            json={
                "chunks": chunks,
                "chunk_count": len(chunks),
                "error": error,
            },
        )
        resp.raise_for_status()
        return resp.json()

    def _process_knowledge_indexing_job(self, job: dict) -> None:
        """Process a knowledge indexing job: download → parse → chunk → embed → sync.

        Downloads the document from VPS, parses it (with OCR fallback via VLM
        if needed — Ollama is available locally), chunks the text, generates
        embeddings (via Ollama nomic-embed-text), and sends the chunks back
        to VPS for storage in the knowledge_chunks table.
        """
        job_id = job["id"]
        doc_info = job.get("document")
        if not doc_info:
            self.submit_job_result(job_id, status="failed", error="No document info in job")
            return

        doc_id = doc_info["id"]
        filename = doc_info.get("filename", f"doc_{doc_id}")

        # Stage 1: Download document from VPS
        self.update_job_status(job_id, status="running", stage="download", progress=0.05)
        self.send_status("busy", f"Baixando documento {filename}", job_id=job_id)
        local_path = self.download_document(doc_info)

        # Stage 2: Confirm download (checksum verification)
        self.update_job_status(job_id, status="running", stage="confirm_download", progress=0.10)
        confirmed = self.confirm_document_download(doc_info, local_path)
        if not confirmed:
            self.submit_job_result(job_id, status="failed", error="Checksum mismatch")
            return

        # Stage 3: Parse + chunk + embed locally
        self.update_job_status(job_id, status="running", stage="knowledge_indexing", progress=0.15)
        self.send_status("busy", f"Indexando {filename}", job_id=job_id)

        from gpcg.infrastructure.document_parser import parse_document, DocumentParseError
        from gpcg.application.knowledge_service import chunk_text, generate_embedding

        # Parse the document (pdfplumber → pypdf → OCR via VLM as last resort)
        try:
            text = parse_document(local_path, file_type=doc_info.get("file_type"))
        except DocumentParseError as e:
            log.error(f"Failed to parse document {filename}: {e}")
            self.submit_indexing_result(doc_id, chunks=[], error=str(e))
            self.submit_job_result(job_id, status="failed", error=str(e))
            try:
                local_path.unlink()
            except OSError:
                pass
            return

        if not text or not text.strip():
            error_msg = f"Document {filename} produced no extractable text"
            log.error(error_msg)
            self.submit_indexing_result(doc_id, chunks=[], error=error_msg)
            self.submit_job_result(job_id, status="failed", error=error_msg)
            try:
                local_path.unlink()
            except OSError:
                pass
            return

        log.info(f"Parsed {filename}: {len(text)} chars of text")

        # Chunk the text
        chunks_data = chunk_text(text)
        log.info(f"Chunked {filename} into {len(chunks_data)} chunks")

        # Generate embeddings for each chunk
        from gpcg.application.knowledge_service import _get_embedding_model
        embed_model = _get_embedding_model()
        total = len(chunks_data)
        chunk_dicts = []
        for i, chunk in enumerate(chunks_data):
            embedding = generate_embedding(chunk.content, chunk.heading_path)
            chunk_dicts.append({
                "content": chunk.content,
                "embedding": embedding,
                "chunk_index": chunk.index,
                "section": chunk.section,
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "embedding_model": embed_model if embedding else None,
            })
            # Update progress (0.15 → 0.90 range)
            if (i + 1) % 5 == 0 or i + 1 == total:
                progress = 0.15 + (i + 1) / total * 0.75
                self.update_job_status(
                    job_id, status="running", stage="knowledge_indexing", progress=progress
                )

        # Stage 4: Submit indexing result (chunks) to VPS
        self.update_job_status(job_id, status="running", stage="knowledge_indexing", progress=0.90)
        result = self.submit_indexing_result(doc_id, chunks=chunk_dicts)
        log.info(f"Synced {len(chunk_dicts)} chunks to VPS for document {doc_id}: {result}")

        # Done
        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(job_id, status="completed", artifacts={
            "indexing_completed": True,
            "chunk_count": len(chunk_dicts),
            "document_id": doc_id,
        })
        log.info(f"Knowledge indexing job #{job_id} completed: {len(chunk_dicts)} chunks")

        # Clean up local document file
        try:
            local_path.unlink()
            log.info(f"Cleaned up document: {local_path.name}")
        except OSError:
            pass
