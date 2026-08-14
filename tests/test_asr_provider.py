"""Tests for ASR provider adapter (Fase 2: multi-worker).

Covers:
- RemoteASRTranscriber: remote ASR via LiteLLM OpenAI-compatible API
- get_asr_transcriber factory: returns correct provider based on config
- Retry on HTTP 429 with Retry-After
- Chunking for long audio files
- AudioSegment format compatibility
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import httpx
import pytest

from gpcg.domain.gameplay_events import AudioSegment
from gpcg.infrastructure.asr_transcriber import (
    ASRTranscriber,
    RemoteASRTranscriber,
    get_asr_transcriber,
    _parse_retry_after,
)


@pytest.fixture(autouse=True)
def reset_settings():
    from gpcg.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def local_asr_env(monkeypatch):
    """Configure for local ASR (default)."""
    monkeypatch.setenv("GPCG_ASR_PROVIDER", "local")
    monkeypatch.setenv("GPCG_GAMEPLAY_ASR_MODEL", "large-v3")
    monkeypatch.setenv("GPCG_GAMEPLAY_ASR_DEVICE", "cuda")
    from gpcg.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def litellm_asr_env(monkeypatch):
    """Configure for remote ASR via LiteLLM."""
    monkeypatch.setenv("GPCG_ASR_PROVIDER", "litellm")
    monkeypatch.setenv("GPCG_LITELLM_BASE_URL", "http://10.0.0.5:4000/v1")
    monkeypatch.setenv("GPCG_LITELLM_API_KEY", "test-key")
    monkeypatch.setenv("GPCG_ASR_MODEL_LITELLM", "whisper")
    monkeypatch.setenv("GPCG_ASR_CHUNK_MINUTES", "5")
    monkeypatch.setenv("GPCG_LLM_MAX_RETRIES", "3")
    from gpcg.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestGetASRTranscriber:
    """Tests for the factory function."""

    def test_returns_local_transcriber(self, local_asr_env):
        transcriber = get_asr_transcriber()
        assert isinstance(transcriber, ASRTranscriber)
        assert not isinstance(transcriber, RemoteASRTranscriber)

    def test_returns_remote_transcriber(self, litellm_asr_env):
        transcriber = get_asr_transcriber()
        assert isinstance(transcriber, RemoteASRTranscriber)

    def test_default_is_local(self, monkeypatch):
        # Don't set GPCG_ASR_PROVIDER — should default to local
        monkeypatch.delenv("GPCG_ASR_PROVIDER", raising=False)
        from gpcg.config import get_settings
        get_settings.cache_clear()
        transcriber = get_asr_transcriber()
        assert isinstance(transcriber, ASRTranscriber)
        get_settings.cache_clear()


class TestRemoteASRTranscriber:
    """Tests for RemoteASRTranscriber."""

    def test_init(self, litellm_asr_env):
        t = RemoteASRTranscriber()
        assert t.base_url == "http://10.0.0.5:4000/v1"
        assert t.api_key == "test-key"
        assert t.model == "whisper"
        assert t.chunk_minutes == 5

    def test_is_available(self, litellm_asr_env):
        t = RemoteASRTranscriber()
        assert t.is_available() is True

    def test_is_available_no_url(self, monkeypatch):
        monkeypatch.setenv("GPCG_LITELLM_BASE_URL", "")
        from gpcg.config import get_settings
        get_settings.cache_clear()
        t = RemoteASRTranscriber()
        assert t.is_available() is False
        get_settings.cache_clear()

    def test_transcribe_single_chunk_with_segments(self, litellm_asr_env, tmp_path):
        """Remote API returns segments with timestamps."""
        # Create a fake audio file
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"fake wav content")

        t = RemoteASRTranscriber()
        t.chunk_minutes = 60  # avoid chunking

        # Mock the chunking to return single chunk
        with patch.object(t, "_chunk_audio", return_value=[(b"fake wav", 0.0)]):
            # Mock the HTTP response
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "text": "full text",
                "language": "pt",
                "segments": [
                    {"start": 0.0, "end": 2.5, "text": "hello world"},
                    {"start": 2.5, "end": 5.0, "text": "second segment"},
                ],
            }
            mock_response.raise_for_status = MagicMock()

            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=None)
                mock_client.post.return_value = mock_response
                mock_client_cls.return_value = mock_client

                segments = t.transcribe(audio_path, language="pt")

        assert len(segments) == 2
        assert segments[0].start == 0.0
        assert segments[0].end == 2.5
        assert segments[0].text == "hello world"
        assert segments[0].language == "pt"
        assert segments[1].start == 2.5
        assert segments[1].text == "second segment"

    def test_transcribe_text_only_response(self, litellm_asr_env, tmp_path):
        """Remote API returns only text (no segments)."""
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"fake wav")

        t = RemoteASRTranscriber()
        t.chunk_minutes = 60

        with patch.object(t, "_chunk_audio", return_value=[(b"fake wav", 0.0)]):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "text": "just text, no segments",
                "language": "en",
            }
            mock_response.raise_for_status = MagicMock()

            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=None)
                mock_client.post.return_value = mock_response
                mock_client_cls.return_value = mock_client

                segments = t.transcribe(audio_path)

        assert len(segments) == 1
        assert segments[0].text == "just text, no segments"
        assert segments[0].language == "en"

    def test_transcribe_with_chunk_offset(self, litellm_asr_env, tmp_path):
        """Timestamps should be offset by chunk start time."""
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"fake wav")

        t = RemoteASRTranscriber()

        # Mock chunking: 2 chunks, first at 0s, second at 300s (5 min)
        with patch.object(t, "_chunk_audio", return_value=[
            (b"chunk1", 0.0),
            (b"chunk2", 300.0),
        ]):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "text": "text",
                "segments": [{"start": 1.0, "end": 3.0, "text": "seg"}],
            }
            mock_response.raise_for_status = MagicMock()

            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=None)
                mock_client.post.return_value = mock_response
                mock_client_cls.return_value = mock_client

                segments = t.transcribe(audio_path)

        assert len(segments) == 2
        # First chunk: offset 0
        assert segments[0].start == 1.0
        assert segments[0].end == 3.0
        # Second chunk: offset 300
        assert segments[1].start == 301.0
        assert segments[1].end == 303.0

    def test_transcribe_file_not_found(self, litellm_asr_env, tmp_path):
        t = RemoteASRTranscriber()
        with pytest.raises(FileNotFoundError):
            t.transcribe(tmp_path / "nonexistent.wav")

    def test_transcribe_with_fallback(self, litellm_asr_env, tmp_path):
        """transcribe_with_fallback should return empty list on error."""
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"fake")

        t = RemoteASRTranscriber()
        with patch.object(t, "transcribe", side_effect=RuntimeError("API error")):
            result = t.transcribe_with_fallback(audio_path)
        assert result == []

    def test_retry_on_429(self, litellm_asr_env, tmp_path):
        """Should retry on HTTP 429."""
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"fake")

        t = RemoteASRTranscriber()
        t.max_retries = 3

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"retry-after": "0"}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"text": "ok", "segments": []}
        resp_200.raise_for_status = MagicMock()

        with patch.object(t, "_chunk_audio", return_value=[(b"fake", 0.0)]):
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=None)
                mock_client.post.side_effect = [resp_429, resp_200]
                mock_client_cls.return_value = mock_client

                with patch("time.sleep"):
                    segments = t.transcribe(audio_path)

        assert segments == []

    def test_max_retries_exceeded(self, litellm_asr_env, tmp_path):
        """Should raise after max retries."""
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"fake")

        t = RemoteASRTranscriber()
        t.max_retries = 2

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"retry-after": "0"}

        with patch.object(t, "_chunk_audio", return_value=[(b"fake", 0.0)]):
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=None)
                mock_client.post.return_value = resp_429
                mock_client_cls.return_value = mock_client

                with patch("time.sleep"):
                    with pytest.raises(RuntimeError, match="failed after 2 retries"):
                        t.transcribe(audio_path)


class TestParseRetryAfterASR:
    """Tests for _parse_retry_after in asr_transcriber."""

    def test_seconds(self):
        assert _parse_retry_after("60") == 60.0

    def test_empty(self):
        assert _parse_retry_after("") is None

    def test_invalid(self):
        assert _parse_retry_after("garbage") is None
