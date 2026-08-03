"""Local LLM client — Ollama-backed, OpenAI-compatible chat.

Follows the ecosystem convention: Ollama at localhost:11434, with
llama3.1:8b for text and gemma3:12b for vision (multimodal).
"""

from __future__ import annotations

import json
import base64
from pathlib import Path
from typing import Optional

import httpx

from gpcg.config import get_settings
from gpcg.logging import get_logger

log = get_logger(__name__)


class LLMError(Exception):
    pass


class LLMClient:
    """Thin Ollama client with JSON-mode and vision support."""

    def __init__(self) -> None:
        s = get_settings()
        self.host = s.ollama_host.rstrip("/")
        self.text_model = s.gpcg_llm_model
        self.vlm_model = s.gpcg_vlm_model
        self.timeout = s.gpcg_llm_timeout

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

        try:
            resp = httpx.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed: {e}") from e

        data = resp.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            raise LLMError(f"empty response from Ollama: {data}")
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

    def vision(
        self,
        images: list[Path],
        prompt: str,
        *,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """Send images to a multimodal model (gemma3:12b) and get a text response."""
        model = model or self.vlm_model
        url = f"{self.host}/api/chat"
        # Ollama expects base64 images (without data URI prefix)
        image_contents = []
        for img in images:
            with open(img, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            image_contents.append(b64)

        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt, "images": image_contents},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        try:
            resp = httpx.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama vision request failed: {e}") from e

        data = resp.json()
        content = data.get("message", {}).get("content", "")
        if not content:
            raise LLMError(f"empty vision response: {data}")
        return content

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

    def embed(
        self,
        text: str,
        *,
        model: str = "nomic-embed-text",
    ) -> list[float]:
        """Generate an embedding vector for a text via Ollama.

        Uses the Ollama /api/embeddings endpoint.
        Returns a list of floats (dimension depends on the model).
        """
        url = f"{self.host}/api/embeddings"
        payload = {
            "model": model,
            "prompt": text,
        }
        try:
            resp = httpx.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama embeddings request failed: {e}") from e

        data = resp.json()
        embedding = data.get("embedding", [])
        if not embedding:
            raise LLMError(f"empty embedding from Ollama: {data}")
        return embedding


_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
