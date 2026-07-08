"""
services/llm_clients.py
Multi-provider LLM client.
Supports Groq, Anthropic, OpenAI, Together AI, Mistral.
Fetches available models dynamically from each provider's API.
"""

import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Per-thread LLM call log ───────────────────────────────────────────────────
_thread_local = threading.local()


def set_llm_log_path(path: str):
    """Set the log file path for LLM calls in the current thread."""
    _thread_local.log_path = path


def _write_llm_log(label: str, provider: str, model: str,
                   system: str, prompt: str, output: str, elapsed: float,
                   prompt_tokens: int = 0, completion_tokens: int = 0):
    log_path = getattr(_thread_local, "log_path", None)
    if not log_path:
        return
    try:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        sep  = "=" * 80
        thin = "-" * 80
        total_tokens = prompt_tokens + completion_tokens
        entry = (
            f"\n{sep}\n"
            f"[{ts}]  {label}\n"
            f"Provider          : {provider}\n"
            f"Model             : {model}\n"
            f"Duration          : {elapsed:.2f}s\n"
            f"Tokens (prompt)   : {prompt_tokens}\n"
            f"Tokens (output)   : {completion_tokens}\n"
            f"Tokens (total)    : {total_tokens}\n"
            f"{thin}\n"
            f"SYSTEM:\n{system}\n"
            f"{thin}\n"
            f"INPUT:\n{prompt}\n"
            f"{thin}\n"
            f"OUTPUT:\n{output}\n"
            f"{sep}\n"
        )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        logger.warning(f"[LLMLog] Failed to write: {e}")

# ── Model filter — exclude non-text-generation models ────────────────────────
_EXCLUDE_PATTERNS = [
    r"whisper", r"tts", r"embed", r"dall-e", r"stable",
    r"guard", r"classifier", r"moderat", r"vision(?!.*instruct)",
    r"realtime", r"transcri", r"audio", r"image",
]

def _is_chat_model(model_id: str) -> bool:
    mid = model_id.lower()
    return not any(re.search(p, mid) for p in _EXCLUDE_PATTERNS)


# ── Anthropic hardcoded models (no public list API) ──────────────────────────
ANTHROPIC_MODELS = [
    "claude-opus-4-5",
    "claude-sonnet-4-5",
    "claude-haiku-4-5-20251001",
    "claude-3-5-sonnet-20241022",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
    "claude-3-haiku-20240307",
]
CLAUDE_MODELS = ANTHROPIC_MODELS  # alias — same key, same API


# ── Fetch models from each provider ──────────────────────────────────────────

def fetch_groq_models(api_key: str) -> list[str]:
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        models = client.models.list()
        return sorted(
            m.id for m in models.data
            if _is_chat_model(m.id)
        )
    except Exception as e:
        logger.warning(f"Could not fetch Groq models: {e}")
        return ["llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                "meta-llama/llama-4-scout-17b-16e-instruct", "qwen/qwen3-32b"]


def fetch_openai_models(api_key: str) -> list[str]:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        models = client.models.list()
        # Only keep GPT and O-series chat models
        gpt_models = sorted(
            m.id for m in models.data
            if _is_chat_model(m.id) and
            any(m.id.startswith(p) for p in ("gpt-", "o1", "o3", "o4", "chatgpt"))
        )
        return gpt_models
    except Exception as e:
        logger.warning(f"Could not fetch OpenAI models: {e}")
        return ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-mini", "o3-mini"]


def fetch_together_models(api_key: str) -> list[str]:
    try:
        import requests
        resp = requests.get(
            "https://api.together.xyz/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        models = []
        for m in data:
            mid = m.get("id", "")
            mtype = m.get("type", "")
            if _is_chat_model(mid) and mtype in ("chat", "language"):
                models.append(mid)
        return sorted(models)
    except Exception as e:
        logger.warning(f"Could not fetch Together models: {e}")
        return [
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            "mistralai/Mixtral-8x7B-Instruct-v0.1",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ]


def fetch_mistral_models(api_key: str) -> list[str]:
    try:
        from mistralai import Mistral
        client = Mistral(api_key=api_key)
        models = client.models.list()
        return sorted(
            m.id for m in models.data
            if _is_chat_model(m.id)
        )
    except Exception as e:
        logger.warning(f"Could not fetch Mistral models: {e}")
        return [
            "mistral-large-latest",
            "mistral-small-latest",
            "open-mixtral-8x22b",
            "open-mistral-nemo",
            "codestral-latest",
        ]


def get_available_models(
    groq_key: str = None,
    anthropic_key: str = None,
    openai_key: str = None,
    together_key: str = None,
    mistral_key: str = None,
    claude_key: str = None,  # alias for anthropic_key
) -> dict:
    """
    Fetch available models from all providers the user has keys for.
    Returns dict grouped by provider.
    """
    result = {}

    groq_k = groq_key or os.getenv("GROQ_API_KEY")
    if groq_k:
        result["groq"] = fetch_groq_models(groq_k)

    anthropic_k = anthropic_key or claude_key or os.getenv("ANTHROPIC_API_KEY")
    if anthropic_k:
        result["anthropic"] = ANTHROPIC_MODELS
        result["claude"] = CLAUDE_MODELS

    openai_k = openai_key or os.getenv("OPENAI_API_KEY")
    if openai_k:
        result["openai"] = fetch_openai_models(openai_k)

    together_k = together_key or os.getenv("TOGETHER_API_KEY")
    if together_k:
        result["together"] = fetch_together_models(together_k)

    mistral_k = mistral_key or os.getenv("MISTRAL_API_KEY")
    if mistral_k:
        result["mistral"] = fetch_mistral_models(mistral_k)

    # Always include Groq fallback if no keys at all
    if not result:
        result["groq"] = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]

    return result


# ── Unified LLM call ──────────────────────────────────────────────────────────

def call_llm(
    prompt: str,
    system: str = "",
    max_tokens: int = 1500,
    provider: str = "groq",
    model: str = "llama-3.3-70b-versatile",
    groq_key: str = None,
    anthropic_key: str = None,
    openai_key: str = None,
    together_key: str = None,
    mistral_key: str = None,
    label: str = "",
) -> str:
    """
    Call the specified provider+model. Falls back through providers if call fails.
    Every call (success or failure) is written to the per-job LLM log file.
    """
    # Resolve keys — user key takes priority over env var
    _anthropic = anthropic_key or os.getenv("ANTHROPIC_API_KEY")
    keys = {
        "groq":      groq_key      or os.getenv("GROQ_API_KEY"),
        "anthropic": _anthropic,
        "claude":    _anthropic,   # same key, same API
        "openai":    openai_key    or os.getenv("OPENAI_API_KEY"),
        "together":  together_key  or os.getenv("TOGETHER_API_KEY"),
        "mistral":   mistral_key   or os.getenv("MISTRAL_API_KEY"),
    }

    fallback_order = ["groq", "anthropic", "claude", "openai", "together", "mistral"]
    fallback_models = {
        "groq":      "llama-3.3-70b-versatile",
        "anthropic": "claude-haiku-4-5-20251001",
        "claude":    "claude-haiku-4-5-20251001",
        "openai":    "gpt-4o-mini",
        "together":  "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "mistral":   "mistral-small-latest",
    }

    t_start = time.time()
    used_provider, used_model = provider, model
    raw = None

    # Try the selected provider first
    raw = _call_provider(prompt, system, max_tokens, provider, model, keys)

    if raw is None:
        # Fallback chain
        for fb_provider in fallback_order:
            if fb_provider == provider:
                continue
            if not keys.get(fb_provider):
                continue
            logger.info(f"Falling back to {fb_provider}")
            fb_model = fallback_models[fb_provider]
            raw = _call_provider(prompt, system, max_tokens, fb_provider, fb_model, keys)
            if raw is not None:
                used_provider, used_model = fb_provider, fb_model
                break

    if raw is None:
        logger.error("All LLM providers failed. Set at least one API key.")
        raw = ("", 0, 0)

    text, prompt_tokens, completion_tokens = raw
    elapsed = time.time() - t_start
    _write_llm_log(
        label=label or "LLM call",
        provider=used_provider,
        model=used_model,
        system=system,
        prompt=prompt,
        output=text,
        elapsed=elapsed,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return text


def _call_provider(prompt, system, max_tokens, provider, model, keys) -> Optional[tuple]:
    """Returns (text, prompt_tokens, completion_tokens) or None on failure."""
    key = keys.get(provider)
    if not key:
        return None

    try:
        if provider == "groq":
            return _call_groq(prompt, system, max_tokens, model, key)
        elif provider in ("anthropic", "claude"):
            return _call_anthropic(prompt, system, max_tokens, model, key)
        elif provider == "openai":
            return _call_openai(prompt, system, max_tokens, model, key)
        elif provider == "together":
            return _call_together(prompt, system, max_tokens, model, key)
        elif provider == "mistral":
            return _call_mistral(prompt, system, max_tokens, model, key)
    except Exception as e:
        logger.warning(f"{provider} call failed: {e}")
    return None


def _call_groq(prompt, system, max_tokens, model, key) -> tuple:
    from groq import Groq
    client = Groq(api_key=key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, temperature=0.3
    )
    text = resp.choices[0].message.content.strip()
    pt = resp.usage.prompt_tokens if resp.usage else 0
    ct = resp.usage.completion_tokens if resp.usage else 0
    return text, pt, ct


def _call_anthropic(prompt, system, max_tokens, model, key) -> tuple:
    import anthropic
    client = anthropic.Anthropic(api_key=key)
    full = f"{system}\n\n{prompt}" if system else prompt
    resp = client.messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": full}]
    )
    text = resp.content[0].text.strip()
    pt = resp.usage.input_tokens if resp.usage else 0
    ct = resp.usage.output_tokens if resp.usage else 0
    return text, pt, ct


def _call_openai(prompt, system, max_tokens, model, key) -> tuple:
    from openai import OpenAI
    client = OpenAI(api_key=key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, temperature=0.3
    )
    text = resp.choices[0].message.content.strip()
    pt = resp.usage.prompt_tokens if resp.usage else 0
    ct = resp.usage.completion_tokens if resp.usage else 0
    return text, pt, ct


def _call_together(prompt, system, max_tokens, model, key) -> tuple:
    from openai import OpenAI  # Together uses OpenAI-compatible API
    client = OpenAI(api_key=key, base_url="https://api.together.xyz/v1")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=model, messages=messages, max_tokens=max_tokens, temperature=0.3
    )
    text = resp.choices[0].message.content.strip()
    pt = resp.usage.prompt_tokens if resp.usage else 0
    ct = resp.usage.completion_tokens if resp.usage else 0
    return text, pt, ct


def _call_mistral(prompt, system, max_tokens, model, key) -> tuple:
    from mistralai import Mistral
    client = Mistral(api_key=key)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.complete(
        model=model, messages=messages, max_tokens=max_tokens, temperature=0.3
    )
    text = resp.choices[0].message.content.strip()
    pt = resp.usage.prompt_tokens if resp.usage else 0
    ct = resp.usage.completion_tokens if resp.usage else 0
    return text, pt, ct