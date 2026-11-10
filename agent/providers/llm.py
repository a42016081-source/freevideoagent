"""LLM provider chain.

Order (configurable with LLM_ORDER or config.yaml providers.llm_order):
    nvidia -> openai(-compatible) -> pollinations (free, no key) -> ollama

A provider is skipped when it is not configured. If every provider fails,
``LLMUnavailable`` is raised and callers fall back to templates, so the agent
never crashes just because no LLM is reachable.

NVIDIA and "openai" both speak the OpenAI chat-completions protocol, so any
other OpenAI-compatible service (Groq, OpenRouter, Together, ...) can be used
through OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL.
"""
from __future__ import annotations

import json
import logging
import time

import requests

from agent.config import settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    pass


def _post(url: str, *, headers=None, payload=None, timeout=60, retries=2):
    """POST with a small exponential back-off on 429 / 5xx / network errors."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt * 1.5)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            if attempt < retries:
                time.sleep(2 ** attempt * 1.5)
    raise last or RuntimeError("request failed")


def _chat_openai_style(base_url: str, key: str, model: str, messages: list[dict],
                       temperature: float, timeout: int) -> str:
    r = _post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        payload={"model": model, "messages": messages, "temperature": temperature,
                 "max_tokens": 2048},
        timeout=timeout,
    )
    return r.json()["choices"][0]["message"]["content"]


def _nvidia(messages, temperature, timeout):
    if not settings.NVIDIA_API_KEY:
        return None
    return _chat_openai_style(settings.NVIDIA_BASE_URL, settings.NVIDIA_API_KEY,
                              settings.NVIDIA_MODEL, messages, temperature, timeout)


def _openai(messages, temperature, timeout):
    if not (settings.OPENAI_BASE_URL and settings.OPENAI_API_KEY):
        return None
    return _chat_openai_style(settings.OPENAI_BASE_URL, settings.OPENAI_API_KEY,
                              settings.OPENAI_MODEL, messages, temperature, timeout)


def _pollinations(messages, temperature, timeout):
    r = _post("https://text.pollinations.ai/openai",
              headers={"Content-Type": "application/json"},
              payload={"model": "openai", "messages": messages, "temperature": temperature},
              timeout=timeout, retries=1)
    return r.json()["choices"][0]["message"]["content"]


def _ollama(messages, temperature, timeout):
    try:  # fail fast when no Ollama server is running
        requests.get(f"{settings.OLLAMA_URL}/api/tags", timeout=2).raise_for_status()
    except requests.RequestException:
        return None
    r = _post(f"{settings.OLLAMA_URL}/api/chat",
              payload={"model": settings.OLLAMA_MODEL, "messages": messages, "stream": False,
                       "options": {"temperature": temperature}},
              timeout=max(timeout, 180), retries=0)
    return r.json()["message"]["content"]


PROVIDERS = {"nvidia": _nvidia, "openai": _openai, "pollinations": _pollinations, "ollama": _ollama}


def complete_ex(system: str, user: str, *, temperature: float = 0.7,
                timeout: int = 60) -> tuple[str, str]:
    """Return (text, provider_name). Raises LLMUnavailable if all providers fail."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    errors = []
    for name in settings.llm_order:
        fn = PROVIDERS.get(name)
        if not fn:
            continue
        try:
            out = fn(messages, temperature, timeout)
        except Exception as e:  # noqa: BLE001 - provider errors must never crash the agent
            errors.append(f"{name}: {e}")
            log.warning("LLM provider %s failed: %s", name, e)
            continue
        if out and out.strip():
            return out.strip(), name
    raise LLMUnavailable("All LLM providers failed or are not configured. " + " | ".join(errors))


def complete(system: str, user: str, **kw) -> str:
    return complete_ex(system, user, **kw)[0]


def extract_json(text: str):
    """Pull the first JSON object/array out of an LLM reply (handles ``` fences and chatter)."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1:] if "\n" in text else text
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError("no JSON found in LLM output")
