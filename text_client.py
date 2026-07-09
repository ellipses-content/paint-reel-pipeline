"""
Free text (and vision) generation via Pollinations' OpenAI-compatible endpoint.

Replaces the paid Anthropic API across the pipeline. No API key required; a
POLLINATIONS_TOKEN raises rate limits if set.
"""

import os
import time
import base64
from pathlib import Path

import requests

TEXT_ENDPOINT = os.environ.get(
    "POLLINATIONS_TEXT_ENDPOINT", "https://text.pollinations.ai/openai"
).rstrip("/")
TEXT_MODEL = os.environ.get("POLLINATIONS_TEXT_MODEL", "openai")
POLLINATIONS_TOKEN = os.environ.get("POLLINATIONS_TOKEN", "")

TEXT_MAX_RETRIES = 4
TEXT_BACKOFF = 5       # seconds, multiplied by attempt number
TEXT_TIMEOUT = 120


def _post(messages: list, max_tokens: int = None, temperature: float = 0.9) -> str:
    payload = {"model": TEXT_MODEL, "messages": messages}
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if temperature is not None:
        payload["temperature"] = temperature
    headers = {}
    if POLLINATIONS_TOKEN:
        headers["Authorization"] = f"Bearer {POLLINATIONS_TOKEN}"

    last_err = None
    for attempt in range(1, TEXT_MAX_RETRIES + 1):
        try:
            resp = requests.post(TEXT_ENDPOINT, json=payload, headers=headers,
                                 timeout=TEXT_TIMEOUT)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            if not content or not content.strip():
                raise ValueError("empty text response")
            return content.strip()
        except Exception as e:  # noqa: BLE001 — retry on anything transient
            last_err = e
            if attempt < TEXT_MAX_RETRIES:
                wait = TEXT_BACKOFF * attempt
                print(f"    [text retry {attempt}/{TEXT_MAX_RETRIES - 1}] {e}; waiting {wait}s...")
                time.sleep(wait)
    raise RuntimeError(f"text generation failed after {TEXT_MAX_RETRIES} attempts: {last_err}")


def generate_text(system: str, user: str, max_tokens: int = None,
                  temperature: float = 0.9) -> str:
    """Return the model's text reply for a system+user prompt."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    return _post(messages, max_tokens, temperature)


def generate_with_images(user: str, image_paths: list, max_tokens: int = None,
                         temperature: float = 0.3) -> str:
    """Vision call: send text + one or more images, return the model's reply."""
    content = [{"type": "text", "text": user}]
    for p in image_paths:
        b64 = base64.standard_b64encode(Path(p).read_bytes()).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
    return _post([{"role": "user", "content": content}], max_tokens, temperature)
