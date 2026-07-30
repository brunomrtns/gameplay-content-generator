"""ASR transcriber — faster-whisper wrapper for gameplay audio transcription.

Uses faster-whisper (CTranslate2 backend) for GPU-accelerated transcription.
The model is loaded lazily on first use and cached.

Outputs AudioSegment list with timestamps, which the GameplayAnalyzer merges
with visual events to enrich them with transcript text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from gpcg.config import get_settings
from gpcg.domain.gameplay_events import AudioSegment
from gpcg.logging import get_logger

log = get_logger(__name__)


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
