"""ASR transcriber — faster-whisper (local) or LiteLLM (remote).

Uses faster-whisper (CTranslate2 backend) for GPU-accelerated transcription
on Bruno's PC. On the VM (multi-worker), uses remote ASR via LiteLLM's
OpenAI-compatible /audio/transcriptions endpoint.

The model is loaded lazily on first use and cached.

Outputs AudioSegment list with timestamps, which the GameplayAnalyzer merges
with visual events to enrich them with transcript text.
"""

from __future__ import annotations

import base64
import io
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import httpx

from gpcg.config import get_settings
from gpcg.domain.gameplay_events import AudioSegment
from gpcg.logging import get_logger

log = get_logger(__name__)


def _parse_retry_after(value: str) -> Optional[float]:
    """Parse Retry-After header (seconds or HTTP date) into seconds."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(int(value)))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = (dt - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, delta)
    except (TypeError, ValueError):
        pass
    return None


class ASRTranscriber:
    """faster-whisper wrapper for gameplay audio transcription.

    The model is loaded lazily (first transcribe call) and cached on the
    instance. This avoids loading the model if ASR is disabled or the
    gameplay has no audio.
    """

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
    ) -> None:
        s = get_settings()
        self.model_size = model_size or s.gpcg_gameplay_asr_model
        self.device = device or s.gpcg_gameplay_asr_device
        self.compute_type = compute_type or s.gpcg_gameplay_asr_compute_type
        self._model = None  # lazy
        self._available = None  # tri-state: None=untested, True/False

    def is_available(self) -> bool:
        """Check if faster-whisper is importable."""
        if self._available is not None:
            return self._available
        try:
            import faster_whisper  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
            log.warning("faster-whisper not installed — ASR disabled")
        return self._available

    def _load_model(self) -> None:
        """Load the faster-whisper model (lazy, cached)."""
        if self._model is not None:
            return
        if not self.is_available():
            raise RuntimeError("faster-whisper not available")
        from faster_whisper import WhisperModel
        log.info(f"loading ASR model: {self.model_size} ({self.device}/{self.compute_type})")
        self._model = WhisperModel(
            self.model_size,
            device=self.device,
            compute_type=self.compute_type,
        )
        log.info("ASR model loaded")

    def transcribe(self, audio_path: str | Path, language: str = "") -> list[AudioSegment]:
        """Transcribe an audio file and return timed segments.

        Args:
            audio_path: path to WAV file (16kHz mono recommended)
            language: language code (e.g. "pt", "en"). Empty = auto-detect.

        Returns:
            List of AudioSegment with start, end, text, confidence.

        Raises RuntimeError if faster-whisper not available.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"audio file not found: {audio_path}")

        self._load_model()
        assert self._model is not None

        log.info(f"transcribing: {audio_path.name}")
        segments_iter, info = self._model.transcribe(
            str(audio_path),
            language=language or None,  # None = auto-detect
            vad_filter=True,  # filter silence for better timestamps
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5,
        )

        detected_lang = info.language if info else (language or "unknown")
        avg_logprob = info.avg_logprob if info else 0.0
        # Convert log-probability to rough confidence (0-1)
        # avg_logprob is typically -1.0 to 0.0; closer to 0 = more confident
        base_confidence = max(0.0, min(1.0, 1.0 + avg_logprob)) if avg_logprob else 0.5

        segments: list[AudioSegment] = []
        for seg in segments_iter:
            text = seg.text.strip()
            if not text:
                continue
            # Per-segment confidence from logprob
            seg_conf = max(0.0, min(1.0, 1.0 + (seg.avg_logprob or avg_logprob)))
            segments.append(AudioSegment(
                start=seg.start,
                end=seg.end,
                text=text,
                language=detected_lang,
                confidence=seg_conf,
                speaker="",  # faster-whisper basic doesn't do diarization
            ))

        log.info(f"transcribed {len(segments)} segments ({detected_lang})")
        return segments

    def transcribe_with_fallback(
        self,
        audio_path: str | Path,
        language: str = "",
    ) -> list[AudioSegment]:
        """Transcribe with graceful fallback on errors.

        If faster-whisper fails (OOM, model download error, etc.),
        returns an empty list rather than crashing the analysis pipeline.
        """
        try:
            return self.transcribe(audio_path, language=language)
        except Exception as e:
            log.error(f"ASR transcription failed: {e}")
            return []


class RemoteASRTranscriber:
    """Remote ASR transcriber using LiteLLM's OpenAI-compatible API.

    Sends audio files to POST {base_url}/audio/transcriptions and parses
    the response into AudioSegment objects. Supports chunking for long
    audio files to avoid API file size limits.

    Used on the VM worker where faster-whisper is not installed.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        chunk_minutes: Optional[int] = None,
    ) -> None:
        s = get_settings()
        self.base_url = (base_url or s.gpcg_litellm_base_url).rstrip("/")
        self.api_key = api_key or s.gpcg_litellm_api_key
        self.model = model or s.gpcg_asr_model_litellm
        self.chunk_minutes = chunk_minutes or s.gpcg_asr_chunk_minutes
        self.timeout = s.gpcg_llm_timeout
        self.max_retries = s.gpcg_llm_max_retries

    def is_available(self) -> bool:
        """Check if the remote ASR endpoint is configured."""
        return bool(self.base_url)

    def _headers(self) -> dict:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def _post_with_retry(self, url: str, files: dict, data: dict) -> dict:
        """POST multipart with retry on 429."""
        headers = self._headers()
        for attempt in range(self.max_retries):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(url, files=files, data=data, headers=headers)
            except httpx.HTTPError as e:
                delay = min(2 ** attempt, 60)
                log.warning(f"ASR request error (attempt {attempt+1}): {e}, retry in {delay}s")
                time.sleep(delay)
                continue

            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp.headers.get("retry-after", ""))
                if retry_after is None:
                    retry_after = min(2 ** attempt, 60)
                log.warning(f"ASR rate limited, retry in {retry_after:.0f}s")
                time.sleep(retry_after)
                continue

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise RuntimeError(f"ASR request failed: {e}") from e

            return resp.json()

        raise RuntimeError(f"ASR request failed after {self.max_retries} retries")

    def _transcribe_chunk(
        self, audio_bytes: bytes, filename: str, language: str = "",
    ) -> list[AudioSegment]:
        """Transcribe a single audio chunk via remote API.

        Returns segments with timestamps relative to the chunk start (0-based).
        """
        url = f"{self.base_url}/audio/transcriptions"
        files = {"file": (filename, audio_bytes, "audio/wav")}
        data = {"model": self.model}
        if language:
            data["language"] = language

        result = self._post_with_retry(url, files, data)

        # Parse response — OpenAI-compatible format
        # The response has "text" field with full transcription.
        # Some APIs also return "segments" with timestamps.
        segments: list[AudioSegment] = []

        if "segments" in result:
            # Detailed segment format (OpenAI Whisper API)
            for seg in result["segments"]:
                text = seg.get("text", "").strip()
                if not text:
                    continue
                segments.append(AudioSegment(
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", 0.0)),
                    text=text,
                    language=language or result.get("language", ""),
                    confidence=float(seg.get("avg_logprob", 0.0)) if seg.get("avg_logprob") else 0.5,
                    speaker="",
                ))
        elif "text" in result:
            # Simple text-only response — create one segment
            text = result["text"].strip()
            if text:
                segments.append(AudioSegment(
                    start=0.0,
                    end=0.0,  # unknown duration
                    text=text,
                    language=language or result.get("language", ""),
                    confidence=0.5,
                    speaker="",
                ))

        return segments

    def _chunk_audio(self, audio_path: Path) -> list[tuple[bytes, float]]:
        """Split audio file into chunks using FFmpeg.

        Returns list of (audio_bytes, chunk_start_seconds) tuples.
        """
        import subprocess
        import tempfile

        chunk_sec = self.chunk_minutes * 60
        chunks: list[tuple[bytes, float]] = []

        # Get total duration
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(audio_path)],
                capture_output=True, text=True, timeout=30,
            )
            duration = float(result.stdout.strip()) if result.stdout.strip() else 0.0
        except (subprocess.TimeoutExpired, ValueError):
            log.warning("Could not get audio duration, sending as single chunk")
            with open(audio_path, "rb") as f:
                return [(f.read(), 0.0)]

        if duration <= chunk_sec:
            # No chunking needed
            with open(audio_path, "rb") as f:
                return [(f.read(), 0.0)]

        # Split with FFmpeg
        num_chunks = int(duration // chunk_sec) + 1
        log.info(f"Chunking {audio_path.name} ({duration:.1f}s) into {num_chunks} chunks")

        for i in range(num_chunks):
            start = i * chunk_sec
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(start), "-t", str(chunk_sec),
                     "-i", str(audio_path), "-ar", "16000", "-ac", "1",
                     str(tmp_path)],
                    capture_output=True, timeout=120,
                )
                if tmp_path.exists() and tmp_path.stat().st_size > 0:
                    with open(tmp_path, "rb") as f:
                        chunks.append((f.read(), float(start)))
            finally:
                if tmp_path.exists():
                    tmp_path.unlink()

        return chunks

    def transcribe(self, audio_path: str | Path, language: str = "") -> list[AudioSegment]:
        """Transcribe an audio file via remote ASR.

        For long audio, splits into chunks and offsets timestamps accordingly.
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"audio file not found: {audio_path}")

        log.info(f"remote transcribing: {audio_path.name}")

        chunks = self._chunk_audio(audio_path)
        all_segments: list[AudioSegment] = []

        for i, (audio_bytes, chunk_start) in enumerate(chunks):
            filename = f"chunk_{i}.wav"
            log.info(f"Transcribing chunk {i+1}/{len(chunks)} (offset {chunk_start:.1f}s)")
            segs = self._transcribe_chunk(audio_bytes, filename, language=language)
            # Offset timestamps by chunk start
            for seg in segs:
                seg.start += chunk_start
                seg.end += chunk_start
                all_segments.append(seg)

        log.info(f"remote transcribed {len(all_segments)} segments")
        return all_segments

    def transcribe_with_fallback(
        self,
        audio_path: str | Path,
        language: str = "",
    ) -> list[AudioSegment]:
        """Transcribe with graceful fallback on errors."""
        try:
            return self.transcribe(audio_path, language=language)
        except Exception as e:
            log.error(f"Remote ASR transcription failed: {e}")
            return []


def get_asr_transcriber():
    """Factory: returns the appropriate ASR transcriber based on config.

    - gpcg_asr_provider="local": ASRTranscriber (faster-whisper)
    - gpcg_asr_provider="litellm": RemoteASRTranscriber (remote)
    """
    s = get_settings()
    if s.gpcg_asr_provider == "litellm":
        return RemoteASRTranscriber()
    return ASRTranscriber()
