"""Tests for LLM provider adapter and retry logic (Fase 1: multi-worker).

Covers:
- _parse_retry_after: seconds and HTTP date formats
- LLMClient with provider=ollama (default, backward compat)
- LLMClient with provider=litellm (remote, OpenAI-compatible)
- Retry on HTTP 429 with Retry-After header
- Retry on HTTP 429 without Retry-After (exponential backoff)
- unload_model is no-op in litellm mode
- embed() works in both modes
- vision() format differs between providers
"""

import json
import time
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
import pytest

from gpcg.infrastructure.llm import (
    LLMClient,
    LLMError,
    _parse_retry_after,
    get_llm,
)


@pytest.fixture(autouse=True)
def reset_llm_singleton():
    """Reset the LLM singleton between tests."""
    import gpcg.infrastructure.llm as llm_mod
    llm_mod._client = None
    yield
    llm_mod._client = None


@pytest.fixture
def ollama_env(monkeypatch):
    """Configure for Ollama provider (default)."""
    monkeypatch.setenv("GPCG_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
    monkeypatch.setenv("GPCG_LLM_MODEL", "llama3.1:8b")
    monkeypatch.setenv("GPCG_VLM_MODEL", "gemma3:12b")
    monkeypatch.setenv("GPCG_LLM_TIMEOUT", "180")
    monkeypatch.setenv("GPCG_LLM_MAX_RETRIES", "3")
    from gpcg.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def litellm_env(monkeypatch):
    """Configure for LiteLLM provider (remote)."""
    monkeypatch.setenv("GPCG_LLM_PROVIDER", "litellm")
    monkeypatch.setenv("GPCG_LITELLM_BASE_URL", "http://10.0.0.5:4000/v1")
    monkeypatch.setenv("GPCG_LITELLM_API_KEY", "test-key")
    monkeypatch.setenv("GPCG_LLM_MODEL_LITELLM", "ollama/llama3.1:8b")
    monkeypatch.setenv("GPCG_VLM_MODEL_LITELLM", "ollama/gemma3:12b")
    monkeypatch.setenv("GPCG_LLM_TIMEOUT", "180")
    monkeypatch.setenv("GPCG_LLM_MAX_RETRIES", "3")
    from gpcg.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestParseRetryAfter:
    """Tests for _parse_retry_after function."""

    def test_seconds_format(self):
        assert _parse_retry_after("120") == 120.0
        assert _parse_retry_after("0") == 0.0
        assert _parse_retry_after("5") == 5.0

    def test_http_date_format(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=60)
        date_str = format_datetime(future, usegmt=True)
        result = _parse_retry_after(date_str)
        assert result is not None
        assert 55 <= result <= 65  # approximately 60s

    def test_empty_value(self):
        assert _parse_retry_after("") is None
        assert _parse_retry_after(None) is None

    def test_invalid_value(self):
        assert _parse_retry_after("not-a-number-or-date") is None

    def test_past_date(self):
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        date_str = format_datetime(past, usegmt=True)
        result = _parse_retry_after(date_str)
        assert result == 0.0  # clamped to 0


class TestLLMClientOllama:
    """Tests for LLMClient in Ollama mode (backward compat)."""

    def test_init_ollama(self, ollama_env):
        client = LLMClient()
        assert client.provider == "ollama"
        assert client.host == "http://localhost:11434"
        assert client.text_model == "llama3.1:8b"
        assert client.vlm_model == "gemma3:12b"
        assert client._is_litellm is False

    def test_chat_ollama(self, ollama_env):
        client = LLMClient()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Hello from Ollama"}
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response):
            result = client.chat("system", "prompt")
        assert result == "Hello from Ollama"

    def test_chat_ollama_json_mode(self, ollama_env):
        client = LLMClient()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": '{"key": "value"}'}
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = client.chat_json("system", "prompt")
            # Check that format=json was in the payload
            call_args = mock_post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["format"] == "json"
        assert result == {"key": "value"}

    def test_unload_model_ollama(self, ollama_env):
        client = LLMClient()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            client.unload_model("llama3.1:8b")
            assert mock_post.called

    def test_embed_ollama(self, ollama_env):
        client = LLMClient()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"embedding": [0.1, 0.2, 0.3]}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = client.embed("test text")
            call_args = mock_post.call_args
            url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
            assert "/api/embeddings" in url
        assert result == [0.1, 0.2, 0.3]


class TestLLMClientLiteLLM:
    """Tests for LLMClient in LiteLLM mode (remote)."""

    def test_init_litellm(self, litellm_env):
        client = LLMClient()
        assert client.provider == "litellm"
        assert client.host == "http://10.0.0.5:4000/v1"
        assert client.api_key == "test-key"
        assert client.text_model == "ollama/llama3.1:8b"
        assert client.vlm_model == "ollama/gemma3:12b"
        assert client._is_litellm is True

    def test_init_litellm_no_base_url(self, monkeypatch):
        monkeypatch.setenv("GPCG_LLM_PROVIDER", "litellm")
        monkeypatch.setenv("GPCG_LITELLM_BASE_URL", "")
        from gpcg.config import get_settings
        get_settings.cache_clear()
        with pytest.raises(LLMError, match="base_url is empty"):
            LLMClient()
        get_settings.cache_clear()

    def test_chat_litellm(self, litellm_env):
        client = LLMClient()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello from LiteLLM"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = client.chat("system", "prompt")
            call_args = mock_post.call_args
            url = call_args[0][0] if call_args[0] else ""
            assert "/chat/completions" in url
            # Check Authorization header
            headers = call_args.kwargs.get("headers", {})
            assert headers.get("Authorization") == "Bearer test-key"
        assert result == "Hello from LiteLLM"

    def test_chat_litellm_json_mode(self, litellm_env):
        client = LLMClient()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"key": "value"}'}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = client.chat_json("system", "prompt")
            call_args = mock_post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["response_format"] == {"type": "json_object"}
        assert result == {"key": "value"}

    def test_unload_model_litellm_noop(self, litellm_env):
        client = LLMClient()
        with patch("httpx.post") as mock_post:
            client.unload_model("any-model")
            assert not mock_post.called  # no-op

    def test_unload_all_models_litellm_noop(self, litellm_env):
        client = LLMClient()
        with patch("httpx.get") as mock_get:
            client.unload_all_models()
            assert not mock_get.called  # no-op

    def test_embed_litellm(self, litellm_env):
        client = LLMClient()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [{"embedding": [0.4, 0.5, 0.6]}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = client.embed("test text")
            call_args = mock_post.call_args
            url = call_args[0][0] if call_args[0] else ""
            assert "/embeddings" in url
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["input"] == "test text"
        assert result == [0.4, 0.5, 0.6]

    def test_vision_litellm_format(self, litellm_env, tmp_path):
        """Vision in LiteLLM mode should use OpenAI-compatible image_url format."""
        client = LLMClient()
        # Create a fake image
        img_path = tmp_path / "test.jpg"
        img_path.write_bytes(b"fake image data")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "I see a game"}}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = client.vision([img_path], "What do you see?")
            call_args = mock_post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            # Check OpenAI vision format
            msg = payload["messages"][0]
            content = msg["content"]
            assert isinstance(content, list)
            assert content[0]["type"] == "text"
            assert content[1]["type"] == "image_url"
            assert "base64" in content[1]["image_url"]["url"]
        assert result == "I see a game"


class TestRetryLogic:
    """Tests for retry on HTTP 429."""

    def test_retry_with_retry_after_header(self, ollama_env):
        """Should wait Retry-After seconds on 429."""
        client = LLMClient()
        client.max_retries = 3

        # First response: 429 with Retry-After: 0 (to avoid real sleep)
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"retry-after": "0"}

        # Second response: 200 OK
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"message": {"content": "success"}}
        resp_200.raise_for_status = MagicMock()

        with patch("httpx.post", side_effect=[resp_429, resp_200]):
            with patch("time.sleep") as mock_sleep:
                result = client.chat("system", "prompt")
                mock_sleep.assert_called_once_with(0.0)
        assert result == "success"

    def test_retry_without_retry_after_uses_backoff(self, ollama_env):
        """Should use exponential backoff when Retry-After is absent."""
        client = LLMClient()
        client.max_retries = 3

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"message": {"content": "success"}}
        resp_200.raise_for_status = MagicMock()

        with patch("httpx.post", side_effect=[resp_429, resp_200]):
            with patch("time.sleep") as mock_sleep:
                result = client.chat("system", "prompt")
                # First retry: 2^0 = 1s
                mock_sleep.assert_called_once_with(1)
        assert result == "success"

    def test_max_retries_exceeded(self, ollama_env):
        """Should raise LLMError after max retries."""
        client = LLMClient()
        client.max_retries = 2

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"retry-after": "0"}

        with patch("httpx.post", return_value=resp_429):
            with patch("time.sleep"):
                with pytest.raises(LLMError, match="failed after 2 retries"):
                    client.chat("system", "prompt")

    def test_non_429_error_raises_immediately(self, ollama_env):
        """Non-429 errors should raise immediately without retry."""
        client = LLMClient()

        resp_500 = MagicMock()
        resp_500.status_code = 500
        resp_500.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=resp_500
        )

        with patch("httpx.post", return_value=resp_500):
            with pytest.raises(LLMError, match="LLM request failed"):
                client.chat("system", "prompt")

    def test_retry_after_http_date(self, ollama_env):
        """Retry-After as HTTP date should be parsed correctly."""
        client = LLMClient()
        client.max_retries = 3

        future = datetime.now(timezone.utc) + timedelta(seconds=0)  # 0s to avoid real sleep
        date_str = format_datetime(future, usegmt=True)

        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.headers = {"retry-after": date_str}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"message": {"content": "ok"}}
        resp_200.raise_for_status = MagicMock()

        with patch("httpx.post", side_effect=[resp_429, resp_200]):
            with patch("time.sleep") as mock_sleep:
                result = client.chat("system", "prompt")
                mock_sleep.assert_called_once()
                delay = mock_sleep.call_args[0][0]
                assert 0 <= delay <= 2  # approximately 0
        assert result == "ok"
