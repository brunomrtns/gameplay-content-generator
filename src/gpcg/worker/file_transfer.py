"""File-transfer mixin for :class:`RemoteWorker`.

Extracted from ``remote_worker.py``. Groups all gameplay/voice/kids-asset
download, checksum confirmation, document download, and video upload methods
so the core worker module stays focused on lifecycle and dispatch.

The mixin assumes the host class provides:
  - ``self.client`` (``httpx.Client``)
  - ``self.config`` (``WorkerConfig``)
  - ``self.storage_root`` (``pathlib.Path``)
  - ``self._current_job`` (``Optional[dict]``)
  - ``self.send_status(...)`` method
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


class FileTransferMixin:
    """Download/upload helpers shared by job handlers."""

    # ── Gameplay download ────────────────────────────────────────────────────

    def download_gameplay(self, source: dict) -> Path:
        """Download a gameplay file from VPS to local storage.

        Tries SCP first (faster, more robust for large files — bypasses
        nginx/HTTP). Falls back to HTTP streaming if SCP is not available.

        Returns the local file path. Raises on error.
        """
        source_id = source["id"]
        token = source["upload_token"]
        filename = source["filename"]

        # Local path: /ToshibaHD/gpcg/gameplays/{source_id}_{filename}
        local_path = self.storage_root / "gameplays" / f"{source_id}_{filename}"
        local_path.parent.mkdir(parents=True, exist_ok=True)

        log.info(f"Downloading gameplay #{source_id} ({filename})...")
        self.send_status("busy", f"Baixando {filename}", job_id=self._current_job["id"] if self._current_job else None, activity_key="worker.activity.downloading_file")

        # Try SCP first (bypasses nginx, much more robust for large files)
        if self._try_scp_download(source, local_path):
            file_size = local_path.stat().st_size
            log.info(f"Downloaded {filename} via SCP ({file_size} bytes) → {local_path}")
            return local_path

        # Fallback: HTTP streaming download
        log.info(f"SCP unavailable, falling back to HTTP download for {filename}")
        with self.client.stream(
            "GET",
            f"/api/gameplays/{source_id}/download",
            params={"token": token},
        ) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=1024 * 1024):  # 1MB
                    f.write(chunk)

        file_size = local_path.stat().st_size
        log.info(f"Downloaded {filename} via HTTP ({file_size} bytes) → {local_path}")
        return local_path

    def _try_scp_download(self, source: dict, local_path: Path) -> bool:
        """Try to download via SCP directly from VPS host.

        Returns True if successful, False if SCP is not available.
        Reads SSH config from env vars:
        - GPCG_SSH_HOST: VPS host (default: extracted from vps_url or 10.0.0.1)
        - GPCG_SSH_USER: SSH user (default: root)
        - GPCG_DOCKER_VOLUME: Docker volume mount path on host
          (default: /var/lib/docker/volumes/gpcg_gpcg-data/_data)
        """
        import shutil as _shutil
        import urllib.parse

        if _shutil.which("scp") is None:
            return False

        ssh_host = os.environ.get("GPCG_SSH_HOST", "")
        if not ssh_host:
            # Extract host from vps_url
            parsed = urllib.parse.urlparse(self.config.vps_url)
            ssh_host = parsed.hostname or "10.0.0.1"

        ssh_user = os.environ.get("GPCG_SSH_USER", "root")
        volume_path = os.environ.get(
            "GPCG_DOCKER_VOLUME",
            "/var/lib/docker/volumes/gpcg_gpcg-data/_data",
        )

        # Build remote path from storage_key
        storage_key = source.get("storage_key", "")
        if not storage_key:
            return False

        # storage_key is like "user_2/filename" → temp_uploads/user_2/filename
        remote_rel = f"temp_uploads/{storage_key}"
        remote_path = f"{volume_path}/{remote_rel}"

        ssh_target = f"{ssh_user}@{ssh_host}"
        log.info(f"SCP download: {ssh_target}:{remote_path} → {local_path}")

        try:
            result = subprocess.run(
                ["scp", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
                 f"{ssh_target}:{remote_path}", str(local_path)],
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode != 0:
                log.warning(f"SCP failed (exit {result.returncode}): {result.stderr[:200]}")
                # Clean up partial file
                if local_path.exists():
                    local_path.unlink()
                return False
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.warning(f"SCP error: {e}")
            if local_path.exists():
                local_path.unlink()
            return False

    def _verify_local_file(self, local_path: Path, expected_hash: str) -> bool:
        """Verify that a local file matches the expected SHA256 hash."""
        if not expected_hash or not local_path.exists():
            return False
        log.info(f"Verifying local file checksum for {local_path.name}...")
        sha256 = hashlib.sha256()
        with open(local_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                sha256.update(chunk)
        actual_hash = sha256.hexdigest()
        match = actual_hash.lower() == expected_hash.lower()
        if match:
            log.info(f"Checksum OK for {local_path.name}")
        else:
            log.warning(f"Checksum mismatch for {local_path.name}: expected {expected_hash[:16]}... got {actual_hash[:16]}...")
        return match

    # ── Voice download ─────────────────────────────────────────────────────────

    def _download_voice(self, filename: str, user_id: int, local_path: Path) -> None:
        """Download a voice reference file from VPS to local voices_dir.

        Saves to voices_dir/user_{user_id}/filename to preserve per-user
        isolation. Tries SCP first (same as gameplay download), falls back
        to HTTP via the worker-auth endpoint /api/voices/{filename}/download.
        """
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Try SCP first (same approach as gameplay download)
        if self._try_scp_voice(filename, user_id, local_path):
            log.info(f"Downloaded voice {filename} via SCP → {local_path}")
            return

        # Fallback: HTTP download via worker-auth endpoint
        log.info(f"SCP unavailable, downloading voice {filename} via HTTP")
        resp = self.client.get(
            f"/api/voices/{filename}/download",
            params={"user_id": user_id},
        )
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)
        log.info(f"Downloaded voice {filename} via HTTP → {local_path}")

    def _try_scp_voice(self, filename: str, user_id: int, local_path: Path) -> bool:
        """Try to download a voice file via SCP from VPS Docker volume."""
        import shutil as _shutil
        import urllib.parse

        if _shutil.which("scp") is None:
            return False

        ssh_host = os.environ.get("GPCG_SSH_HOST", "")
        if not ssh_host:
            parsed = urllib.parse.urlparse(self.config.vps_url)
            ssh_host = parsed.hostname or "10.0.0.1"

        ssh_user = os.environ.get("GPCG_SSH_USER", "root")
        volume_path = os.environ.get(
            "GPCG_DOCKER_VOLUME",
            "/var/lib/docker/volumes/gpcg_gpcg-data/_data",
        )

        # Voice files are in data/voices/{user_id_dir}/ or data/voices/
        # Try user-specific dir first, then shared dir
        candidates = [
            f"{volume_path}/voices/user_{user_id}/{filename}",
            f"{volume_path}/voices/{filename}",
        ]

        ssh_target = f"{ssh_user}@{ssh_host}"
        for remote_path in candidates:
            log.info(f"SCP voice: {ssh_target}:{remote_path} → {local_path}")
            try:
                result = subprocess.run(
                    ["scp", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
                     f"{ssh_target}:{remote_path}", str(local_path)],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0 and local_path.exists():
                    return True
                if local_path.exists():
                    local_path.unlink()
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
        return False

    # ── Kids asset download ───────────────────────────────────────────────────

    def _download_kids_assets(self, job_data: dict) -> None:
        """Download Kids story assets (images) from VPS to local storage.

        For Kids jobs, the pipeline needs the images locally to convert them
        to video clips (Ken Burns effect). This downloads each asset via the
        worker-auth endpoint /api/kids/assets/{id}/download and saves to
        {storage_root}/kids_assets/{storage_key}.
        """
        assets = job_data.get("story_assets", [])
        if not assets:
            log.warning("Kids job has no story_assets in job_data — pipeline will have no images")
            return

        kids_dir = self.storage_root / "kids_assets"
        kids_dir.mkdir(parents=True, exist_ok=True)

        for asset in assets:
            asset_id = asset.get("id")
            storage_key = asset.get("storage_key", "")
            filename = asset.get("filename", f"asset_{asset_id}")

            if not asset_id or not storage_key:
                log.warning(f"Skipping Kids asset with missing id/storage_key: {asset}")
                continue

            local_path = kids_dir / storage_key
            if local_path.exists():
                log.info(f"Kids asset #{asset_id} already exists locally: {local_path}")
                continue

            try:
                log.info(f"Downloading Kids asset #{asset_id} ({filename}) from VPS...")
                resp = self.client.get(f"/api/kids/assets/{asset_id}/download")
                resp.raise_for_status()
                with open(local_path, "wb") as f:
                    f.write(resp.content)
                log.info(f"Downloaded Kids asset #{asset_id} → {local_path}")
            except Exception as e:
                log.error(f"Failed to download Kids asset #{asset_id}: {e}")

    # ── Confirm download (checksum) ──────────────────────────────────────────

    def confirm_download(self, source: dict, local_path: Path) -> bool:
        """Verify checksum and confirm download with VPS.

        Returns True if confirmed, False if checksum mismatch.
        """
        source_id = source["id"]
        expected_hash = source["file_hash"]

        # Compute SHA256
        log.info(f"Verifying checksum for {local_path.name}...")
        sha256 = hashlib.sha256()
        with open(local_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                sha256.update(chunk)
        actual_hash = sha256.hexdigest()

        if actual_hash.lower() != expected_hash.lower():
            log.error(
                f"Checksum mismatch for #{source_id}: "
                f"expected={expected_hash[:16]}... got={actual_hash[:16]}..."
            )
            return False

        # Confirm with VPS
        resp = self.client.post(
            f"/api/gameplays/{source_id}/confirm-download",
            json={
                "worker_id": self.config.worker_id,
                "checksum": actual_hash,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        log.info(f"Download confirmed for #{source_id}: {data}")
        return True

    # ── Knowledge document download ──────────────────────────────────────────

    def download_document(self, doc: dict) -> Path:
        """Download a knowledge document from VPS to local storage.

        Returns the local file path. Raises on error.
        """
        doc_id = doc["id"]
        token = doc.get("upload_token")
        if not token:
            raise RuntimeError(f"No upload_token for document {doc_id}")

        filename = doc.get("filename", f"doc_{doc_id}")
        # Sanitize filename for local storage
        safe_name = filename.replace("/", "_").replace("\\", "_")
        local_dir = self.storage_root / "knowledge"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / f"doc_{doc_id}_{safe_name}"

        url = f"/api/documents/{doc_id}/download"
        resp = self.client.get(url, params={"token": token}, follow_redirects=True)
        resp.raise_for_status()

        # Stream response content to file
        with open(local_path, "wb") as f:
            f.write(resp.content)

        file_size = local_path.stat().st_size
        log.info(f"Downloaded document {filename} ({file_size} bytes) → {local_path}")
        return local_path

    def confirm_document_download(self, doc: dict, local_path: Path) -> bool:
        """Verify checksum and confirm document download with VPS."""
        doc_id = doc["id"]
        import hashlib as _hashlib
        sha256 = _hashlib.sha256()
        with open(local_path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha256.update(chunk)
        checksum = sha256.hexdigest()

        resp = self.client.post(
            f"/api/documents/{doc_id}/confirm-download",
            json={
                "checksum": checksum,
                "worker_id": self.config.worker_id,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        log.info(f"Download confirmed for document #{doc_id}: {data}")
        return True

    # ── Upload video ─────────────────────────────────────────────────────────

    def upload_video(self, job_id: int, video_path: Path) -> dict:
        """Upload a rendered video to the VPS."""
        with open(video_path, "rb") as f:
            resp = self.client.post(
                f"/api/jobs/{job_id}/upload-video",
                files={"file": (video_path.name, f, "video/mp4")},
            )
        resp.raise_for_status()
        return resp.json()
