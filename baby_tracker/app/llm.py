"""Pluggable LLM provider for AI daily summaries (SDD-003 §3.1).

One `generate(cfg, prompt, install_token)` entry point dispatches on
`cfg.summary_provider`:

  hosted     -> POST {summary_hosted_url}/summary {install_token, prompt}
                (a dumb, rate-limited relay Albin runs; the default)
  ollama     -> POST {summary_ollama_url}/api/generate  (self-hosted)
  anthropic  -> Claude Messages API      (summary_api_key + summary_model)
  openai     -> Chat Completions at {summary_openai_url} (summary_api_key +
                summary_model). The base URL is configurable, so this provider
                reaches ANY OpenAI-compatible service (OpenRouter, Ollama and
                Ollama Cloud, LiteLLM, LM Studio, vLLM, Groq, Together), not
                just api.openai.com.
  gemini     -> generateContent          (summary_api_key + summary_model)

The prompt is always built by the add-on (instruction + de-identified digest), so
no provider ever receives names or note text. `CapError` signals the hosted
server's 2/day limit (HTTP 429).
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("baby.llm")

TIMEOUT = 60.0
MAX_TOKENS = 400

# Where the `openai` provider points when summary_openai_url is blank.
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"


class CapError(Exception):
    """Hosted provider returned 429 (daily cap reached)."""


class ProviderError(Exception):
    """A provider call failed (network, auth, bad response)."""


async def generate(cfg, prompt: str, install_token: str | None = None) -> str:
    provider = (cfg.summary_provider or "hosted").lower()
    try:
        if provider == "hosted":
            return await _hosted(cfg, prompt, install_token)
        if provider == "ollama":
            return await _ollama(cfg, prompt)
        if provider == "anthropic":
            return await _anthropic(cfg, prompt)
        if provider == "openai":
            return await _openai(cfg, prompt)
        if provider == "gemini":
            return await _gemini(cfg, prompt)
        raise ProviderError(f"unknown provider {provider!r}")
    except CapError:
        raise
    except httpx.HTTPError as e:
        raise ProviderError(str(e)) from e


async def _hosted(cfg, prompt: str, install_token: str | None) -> str:
    base = (cfg.summary_hosted_url or "").rstrip("/")
    if not base:
        raise ProviderError("summary_hosted_url not set")
    body = {"install_token": install_token or "", "prompt": prompt}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(base + "/summary", json=body)
        if r.status_code == 429:
            raise CapError("hosted daily cap reached")
        r.raise_for_status()
        return (r.json().get("summary") or "").strip()


async def _ollama(cfg, prompt: str) -> str:
    url = (cfg.summary_ollama_url or "").rstrip("/") + "/api/generate"
    body = {"model": cfg.summary_model, "prompt": prompt, "stream": False}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(url, json=body)
        r.raise_for_status()
        return (r.json().get("response") or "").strip()


async def _anthropic(cfg, prompt: str) -> str:
    headers = {
        "x-api-key": cfg.summary_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": cfg.summary_model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
                              json=body, headers=headers)
        r.raise_for_status()
        parts = r.json().get("content") or []
        return "".join(p.get("text", "") for p in parts).strip()


def openai_endpoint(cfg) -> str:
    """Resolve the chat-completions URL for the OpenAI-compatible provider.

    Deliberately tolerant about what the user pastes, because every service
    documents its base URL differently and a wrong guess surfaces only as an
    opaque 404. All of these end up at the same call:

      ""                                     -> api.openai.com/v1/chat/completions
      https://openrouter.ai/api              -> .../api/v1/chat/completions
      https://openrouter.ai/api/v1           -> .../api/v1/chat/completions
      http://homeassistant.local:11434/v1/   -> .../v1/chat/completions
      https://x/v1/chat/completions          -> unchanged
    """
    base = (cfg.summary_openai_url or "").strip().rstrip("/") or DEFAULT_OPENAI_BASE
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


async def _openai(cfg, prompt: str) -> str:
    headers = {"Authorization": f"Bearer {cfg.summary_api_key}"}
    body = {
        "model": cfg.summary_model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": prompt}],
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(openai_endpoint(cfg), json=body, headers=headers)
        r.raise_for_status()
        choices = r.json().get("choices") or [{}]
        return (choices[0].get("message", {}).get("content") or "").strip()


async def _gemini(cfg, prompt: str) -> str:
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{cfg.summary_model}:generateContent?key={cfg.summary_api_key}")
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(url, json=body)
        r.raise_for_status()
        cands = r.json().get("candidates") or [{}]
        parts = cands[0].get("content", {}).get("parts") or [{}]
        return (parts[0].get("text") or "").strip()
