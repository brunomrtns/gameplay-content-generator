"""LLM client — Ollama (local) or LiteLLM (remote, OpenAI-compatible).

Follows the ecosystem convention: Ollama at localhost:11434, with
llama3.1:8b for text and gemma3:12b for vision (multimodal).

Multi-worker: when gpcg_llm_provider="litellm", all calls go through
the OpenAI-compatible API at gpcg_litellm_base_url. This allows the
VM worker to use remote LLM/VLM without local Ollama.
"""

from __future__ import annotations

import json
import base64
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from gpcg.config import get_settings
from gpcg.logging import get_logger

log = get_logger(__name__)


class LLMError(Exception):
    pass


def _parse_retry_after(value: str) -> Optional[float]:
    """Parse a Retry-After header value into seconds.

    Supports both formats:
    - Seconds: "120"
    - HTTP date: "Wed, 21 Oct 2025 07:28:00 GMT"
    """
    if not value:
        return None
    value = value.strip()
    # Try integer seconds first
    try:
        secs = int(value)
        return max(0.0, float(secs))
    except ValueError:
        pass
    # Try HTTP date
    try:
        dt = parsedate_to_datetime(value)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            delta = (dt - now).total_seconds()
            return max(0.0, delta)
    except (TypeError, ValueError):
        pass
    return None


class LLMClient:
    """LLM client supporting Ollama (local) or LiteLLM (remote).

    Provider is selected via gpcg_llm_provider config:
    - "ollama": uses Ollama native API at ollama_host
    - "litellm": uses OpenAI-compatible API at gpcg_litellm_base_url

    Both modes support chat(), chat_json(), vision(), vision_json(),
    generate(), and embed(). Ollama-specific methods (unload_model,
    unload_all_models) are no-ops in litellm mode.
    """

    def __init__(self) -> None:
        s = get_settings()
        self.provider = s.gpcg_llm_provider
        self.timeout = s.gpcg_llm_timeout
        self.max_retries = s.gpcg_llm_max_retries

        if self.provider == "litellm":
            self.host = s.gpcg_litellm_base_url.rstrip("/")
            self.api_key = s.gpcg_litellm_api_key
            self.text_model = s.gpcg_llm_model_litellm
            self.vlm_model = s.gpcg_vlm_model_litellm
            if not self.host:
                raise LLMError("gpcg_llm_provider=litellm but gpcg_litellm_base_url is empty")
        else:
            self.host = s.ollama_host.rstrip("/")
            self.api_key = ""
            self.text_model = s.gpcg_llm_model
            self.vlm_model = s.gpcg_vlm_model

    @property
    def _is_litellm(self) -> bool:
        return self.provider == "litellm"

    def _headers(self) -> dict:
        if self._is_litellm and self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def _post_with_retry(self, url: str, payload: dict) -> dict:
        """POST with retry on HTTP 429 (rate limit).

        Respects Retry-After header (seconds or HTTP date).
        Falls back to exponential backoff if header is absent/invalid.
        """
        headers = self._headers()
        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
            except httpx.HTTPError as e:
                last_error = e
                delay = min(2 ** attempt, 60)
                log.warning(f"LLM request error (attempt {attempt+1}/{self.max_retries}): {e}, retrying in {delay}s")
                time.sleep(delay)
                continue

            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp.headers.get("retry-after", ""))
                if retry_after is None:
                    retry_after = min(2 ** attempt, 60)
                log.warning(
                    f"LLM rate limited (429, attempt {attempt+1}/{self.max_retries}), "
                    f"retrying in {retry_after:.0f}s"
                )
                time.sleep(retry_after)
                continue

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise LLMError(f"LLM request failed: {e}") from e

            return resp.json()

        raise LLMError(f"LLM request failed after {self.max_retries} retries: {last_error}")

    def _get_with_retry(self, url: str) -> dict:
        """GET with retry on HTTP 429 (for embed/list endpoints)."""
        headers = self._headers()
        for attempt in range(self.max_retries):
            try:
                resp = httpx.get(url, headers=headers, timeout=self.timeout)
            except httpx.HTTPError as e:
                delay = min(2 ** attempt, 60)
                log.warning(f"LLM GET error (attempt {attempt+1}): {e}, retrying in {delay}s")
                time.sleep(delay)
                continue

            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp.headers.get("retry-after", ""))
                if retry_after is None:
                    retry_after = min(2 ** attempt, 60)
                log.warning(f"LLM GET rate limited, retrying in {retry_after:.0f}s")
                time.sleep(retry_after)
                continue

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise LLMError(f"LLM GET failed: {e}") from e

            return resp.json()

        raise LLMError(f"LLM GET failed after {self.max_retries} retries")

    # ── Chat ──────────────────────────────────────────────────────────────

    def chat(
        self,
        system: str,
        prompt: str,
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        json_mode: bool = False,
        max_tokens: int = 2048,
    ) -> str:
        """Synchronous chat completion. Returns the assistant message text."""
        model = model or self.text_model

        if self._is_litellm:
            return self._chat_litellm(system, prompt, model, temperature, json_mode, max_tokens)
        return self._chat_ollama(system, prompt, model, temperature, json_mode, max_tokens)

    def _chat_ollama(
        self, system: str, prompt: str, model: str,
        temperature: float, json_mode: bool, max_tokens: int,
    ) -> str:
        url = f"{self.host}/api/chat"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"

        data = self._post_with_retry(url, payload)
        content = data.get("message", {}).get("content", "")
        if not content:
            raise LLMError(f"empty response from Ollama: {data}")
        return content

    def _chat_litellm(
        self, system: str, prompt: str, model: str,
        temperature: float, json_mode: bool, max_tokens: int,
    ) -> str:
        url = f"{self.host}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        data = self._post_with_retry(url, payload)
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not content:
            raise LLMError(f"empty response from LiteLLM: {data}")
        return content

    def chat_json(
        self,
        system: str,
        prompt: str,
        *,
        model: Optional[str] = None,
        temperature: float = 0.5,
        max_tokens: int = 2048,
    ) -> dict | list:
        """Chat expecting a JSON response. Parses and returns the object."""
        raw = self.chat(system, prompt, model=model, temperature=temperature, json_mode=True, max_tokens=max_tokens)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON from the text
            text = raw.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            start = text.find("[")
            end = text.rfind("]") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            raise LLMError(f"could not parse JSON from LLM response: {raw[:300]}")

    # ── Vision ────────────────────────────────────────────────────────────

    def vision(
        self,
        images: list[Path],
        prompt: str,
        *,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """Send images to a multimodal model and get a text response."""
        model = model or self.vlm_model

        # Read and encode images
        image_contents = []
        for img in images:
            with open(img, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            image_contents.append(b64)

        if self._is_litellm:
            return self._vision_litellm(image_contents, prompt, model, temperature, max_tokens)
        return self._vision_ollama(image_contents, prompt, model, temperature, max_tokens)

    def _vision_ollama(
        self, images_b64: list[str], prompt: str, model: str,
        temperature: float, max_tokens: int,
    ) -> str:
        url = f"{self.host}/api/chat"
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt, "images": images_b64},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        data = self._post_with_retry(url, payload)
        content = data.get("message", {}).get("content", "")
        if not content:
            raise LLMError(f"empty vision response from Ollama: {data}")
        return content

    def _vision_litellm(
        self, images_b64: list[str], prompt: str, model: str,
        temperature: float, max_tokens: int,
    ) -> str:
        url = f"{self.host}/chat/completions"
        # OpenAI-compatible vision format
        content = [{"type": "text", "text": prompt}]
        for b64 in images_b64:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": content},
            ],
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = self._post_with_retry(url, payload)
        resp_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not resp_content:
            raise LLMError(f"empty vision response from LiteLLM: {data}")
        return resp_content

    def vision_json(
        self,
        images: list[Path],
        prompt: str,
        *,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> dict | list:
        """Vision call expecting JSON response."""
        raw = self.vision(images, prompt, model=model, temperature=temperature, max_tokens=max_tokens)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            text = raw.strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
            raise LLMError(f"could not parse JSON from vision response: {raw[:300]}")

    # ── Generate ──────────────────────────────────────────────────────────

    def generate(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Simple text generation (no system prompt). Returns the assistant text.

        Convenience wrapper around chat() with an empty system prompt.
        """
        return self.chat(
            system="You are a helpful assistant.",
            prompt=prompt,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    # ── Ollama-specific (no-ops in litellm mode) ──────────────────────────

    def unload_model(self, model: str) -> None:
        """Unload a model from Ollama VRAM.

        No-op in litellm mode (remote models are not in local VRAM).
        """
        if self._is_litellm:
            return
        url = f"{self.host}/api/generate"
        payload = {"model": model, "prompt": "", "keep_alive": 0}
        try:
            resp = httpx.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            log.info(f"unloaded Ollama model '{model}' from VRAM")
        except httpx.HTTPError as e:
            log.warning(f"could not unload Ollama model '{model}': {e}")

    def unload_all_models(self) -> None:
        """Unload all currently loaded Ollama models from VRAM.

        No-op in litellm mode.
        """
        if self._is_litellm:
            return
        try:
            resp = httpx.get(f"{self.host}/api/ps", timeout=5)
            resp.raise_for_status()
            data = resp.json()
            models = data.get("models", [])
            for m in models:
                name = m.get("name", "") or m.get("model", "")
                if name:
                    self.unload_model(name)
        except httpx.HTTPError as e:
            log.warning(f"could not list Ollama models for unload: {e}")

    # ── Embeddings ────────────────────────────────────────────────────────

    def embed(
        self,
        text: str,
        *,
        model: str = "nomic-embed-text",
    ) -> list[float]:
        """Generate an embedding vector for a text.

        Ollama: uses /api/embeddings endpoint.
        LiteLLM: uses /embeddings endpoint (OpenAI-compatible).
        """
        if self._is_litellm:
            return self._embed_litellm(text, model)
        return self._embed_ollama(text, model)

    def _embed_ollama(self, text: str, model: str) -> list[float]:
        url = f"{self.host}/api/embeddings"
        payload = {"model": model, "prompt": text}
        data = self._post_with_retry(url, payload)
        embedding = data.get("embedding", [])
        if not embedding:
            raise LLMError(f"empty embedding from Ollama: {data}")
        return embedding

    def _embed_litellm(self, text: str, model: str) -> list[float]:
        url = f"{self.host}/embeddings"
        payload = {"model": model, "input": text}
        data = self._post_with_retry(url, payload)
        embedding = data.get("data", [{}])[0].get("embedding", [])
        if not embedding:
            raise LLMError(f"empty embedding from LiteLLM: {data}")
        return embedding


_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
