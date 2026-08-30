"""Cleanup job handler mixin for :class:`RemoteWorker`.

Extracted from ``remote_worker.py``. Contains the gameplay cleanup and
user storage cleanup pipelines used during domain reset.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


class CleanupMixin:
    """Cleanup job processing (gameplay files + user storage)."""

    def _process_cleanup_gameplay_job(self, job: dict) -> None:
        """Process a cleanup_gameplay job: delete physical files from local storage.

        Removes the gameplay video file, analysis JSON, and any renders
        associated with the source from the worker's HD.
        """
        job_id = job["id"]
        artifacts = job.get("artifacts", {})
        if isinstance(artifacts, str):
            try:
                import json as _json
                artifacts = _json.loads(artifacts)
            except Exception:
                artifacts = {}
        source_id = artifacts.get("source_id") or job.get("gameplay_source_id")
        filename = artifacts.get("filename", "")

        self.update_job_status(job_id, status="running", stage="cleanup", progress=0.1)
        self.send_status("busy", f"Limpando gameplay #{source_id}", job_id=job_id, activity_key="worker.activity.cleaning_gameplay")

        deleted_files: list[str] = []

        # 1. Delete gameplay video file: gameplays/{source_id}_{filename}
        if source_id and filename:
            gameplay_path = self.storage_root / "gameplays" / f"{source_id}_{filename}"
            if gameplay_path.exists():
                try:
                    gameplay_path.unlink()
                    deleted_files.append(str(gameplay_path))
                    log.info(f"Deleted gameplay file: {gameplay_path}")
                except OSError as e:
                    log.warning(f"Failed to delete {gameplay_path}: {e}")

        # 2. Delete analysis JSON: mapped/source_{source_id}_analysis.json
        if source_id:
            analysis_path = self.storage_root / "mapped" / f"source_{source_id}_analysis.json"
            if analysis_path.exists():
                try:
                    analysis_path.unlink()
                    deleted_files.append(str(analysis_path))
                    log.info(f"Deleted analysis file: {analysis_path}")
                except OSError as e:
                    log.warning(f"Failed to delete {analysis_path}: {e}")

        # 3. Delete any renders associated with this source
        # Renders are named by job_id, but we can clean orphans matching source_id
        renders_dir = self.storage_root / "renders"
        if renders_dir.exists():
            for render_file in renders_dir.glob(f"*source_{source_id}*"):
                try:
                    render_file.unlink()
                    deleted_files.append(str(render_file))
                    log.info(f"Deleted render file: {render_file}")
                except OSError as e:
                    log.warning(f"Failed to delete {render_file}: {e}")

        log.info(
            f"Cleanup job #{job_id} completed — deleted {len(deleted_files)} file(s) "
            f"for gameplay source #{source_id}"
        )

        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(
            job_id,
            status="completed",
            artifacts={"deleted_files": deleted_files, "source_id": source_id},
        )

    def _process_cleanup_user_storage_job(self, job: dict) -> None:
        """Process a cleanup_user_storage job: delete ALL files for a user/domain.

        This is the comprehensive storage cleanup triggered by domain reset.
        It removes all files belonging to the user across all worker storage
        directories (gameplays, mapped, renders, outputs).

        SAFETY:
        - Only deletes files within self.storage_root (path traversal protection).
        - Only deletes files matching the user_id prefix (user_{user_id}_ or
          {source_id}_ where source_id belongs to the user — but since we're
          deleting everything, we use the user-scoped subdirectories).
        - Missing files are NOT errors (idempotent).
        - Does NOT touch files belonging to other users.
        """
        job_id = job["id"]
        artifacts = job.get("artifacts", {})
        if isinstance(artifacts, str):
            try:
                import json as _json
                artifacts = _json.loads(artifacts)
            except Exception:
                artifacts = {}

        user_id = artifacts.get("user_id") or job.get("user_id")
        old_domain = artifacts.get("old_domain", "games")
        if not user_id:
            self.submit_job_result(job_id, status="failed", error="No user_id in cleanup job")
            return

        self.update_job_status(job_id, status="running", stage="cleanup", progress=0.1)
        self.send_status("busy", f"Limpando storage {old_domain} do usuário #{user_id}", job_id=job_id, activity_key="worker.activity.cleaning_storage")

        deleted_files: list[str] = []
        errors: list[str] = []

        # Resolve storage_root to an absolute path for safety checks
        storage_root = self.storage_root.resolve()

        def _safe_delete(path: Path) -> None:
            """Delete a file if it exists and is within storage_root. Idempotent."""
            try:
                resolved = path.resolve()
                # Path traversal protection: ensure the path is within storage_root
                if not str(resolved).startswith(str(storage_root)):
                    log.warning(f"Skipping file outside storage_root: {path}")
                    errors.append(f"Path outside storage_root: {path}")
                    return
                if resolved.is_file():
                    resolved.unlink()
                    deleted_files.append(str(resolved))
                    log.info(f"Deleted: {resolved}")
                elif resolved.is_dir():
                    # Only delete empty directories within storage_root
                    try:
                        resolved.rmdir()
                        log.info(f"Removed empty dir: {resolved}")
                    except OSError:
                        pass  # Directory not empty — leave it
            except OSError as e:
                log.warning(f"Failed to delete {path}: {e}")
                errors.append(str(e))

        # Domain-aware cleanup: only clean directories belonging to old_domain.
        # Games: gameplays/, mapped/
        # Kids: kids_assets/
        # Shared (cleaned for both): renders/, outputs/ (per-job, safe to clean)
        #
        # MULTI-USER SAFETY: The cleanup job carries "filenames" and
        # "storage_keys" lists in artifacts — only files matching those
        # names are deleted. This prevents deleting other users' files
        # in a multi-user deployment. If the lists are empty (legacy job
        # or no files to clean), we fall back to deleting all files in
        # the domain-specific directories (single-user backward compat).

        cleanup_filenames = set(artifacts.get("filenames", []))
        cleanup_storage_keys = set(artifacts.get("storage_keys", []))
        # Also derive filenames from storage_keys (worker may store by storage_key)
        cleanup_names = cleanup_filenames | cleanup_storage_keys

        def _should_delete(filename: str) -> bool:
            """Check if a file should be deleted (user-scoped)."""
            if not cleanup_names:
                # No list provided — backward compat: delete all
                return True
            # Check if the filename matches any known filename or storage_key
            # Files are stored as {source_id}_{original_filename} or {hash}_{filename}
            for name in cleanup_names:
                if filename == name or filename.endswith(name) or name in filename:
                    return True
            return False

        if old_domain == "games":
            # 1a. Clean gameplays directory (Games-specific)
            gameplays_dir = storage_root / "gameplays"
            if gameplays_dir.exists():
                for f in gameplays_dir.iterdir():
                    if f.is_file() and _should_delete(f.name):
                        _safe_delete(f)

            # 1b. Clean mapped directory — analysis JSONs (Games-specific)
            mapped_dir = storage_root / "mapped"
            if mapped_dir.exists():
                for f in mapped_dir.iterdir():
                    if f.is_file() and _should_delete(f.name):
                        _safe_delete(f)

        elif old_domain == "kids":
            # Clean kids_assets directory (Kids-specific)
            kids_assets_dir = storage_root / "kids_assets"
            if not kids_assets_dir.exists():
                kids_assets_dir = storage_root / "data" / "kids_assets"
            if kids_assets_dir.exists():
                for f in kids_assets_dir.iterdir():
                    if f.is_file() and _should_delete(f.name):
                        _safe_delete(f)

        # 3. Clean renders directory — intermediate render files (shared)
        renders_dir = storage_root / "renders"
        if renders_dir.exists():
            for f in renders_dir.iterdir():
                if f.is_file():
                    _safe_delete(f)

        # 4. Clean outputs directory — final output files (shared)
        outputs_dir = storage_root / "outputs"
        if outputs_dir.exists():
            for f in outputs_dir.iterdir():
                if f.is_file():
                    _safe_delete(f)

        # 5. Clean per-user voice directory if it exists
        from gpcg.config import get_settings
        try:
            local_settings = get_settings()
            user_voices = local_settings.voices_dir / f"user_{user_id}"
            if user_voices.exists():
                for f in user_voices.iterdir():
                    if f.is_file():
                        _safe_delete(f)
        except Exception as e:
            log.warning(f"Could not clean user voices: {e}")

        log.info(
            f"User storage cleanup #{job_id} completed — "
            f"deleted {len(deleted_files)} file(s) for user #{user_id}"
        )

        self.update_job_status(job_id, status="running", stage="done", progress=1.0)
        self.submit_job_result(
            job_id,
            status="completed",
            artifacts={
                "deleted_files": deleted_files,
                "user_id": user_id,
                "errors": errors,
                "deleted_count": len(deleted_files),
            },
        )
