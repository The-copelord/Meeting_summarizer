"""
services/summarizer.py
LLM-based hierarchical summarization using multi-provider client.
"""

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Prompts ───────────────────────────────────────────────────────────────────

CHUNK_SUMMARY_SYSTEM = """You are an expert meeting analyst.
Summarize the provided meeting transcript chunk concisely and accurately.
Focus on: key topics, important statements, decisions, and action items.
Be factual and preserve speaker attributions when relevant."""

CHUNK_SUMMARY_PROMPT = """Below is a portion of a meeting transcript. Summarize the key points:

<transcript>
{transcript}
</transcript>

Provide a concise summary (3-6 sentences) of what was discussed."""

FINAL_SUMMARY_SYSTEM = """You are a professional meeting secretary producing structured meeting notes.
Analyze the chunk summaries and extract key information.
Respond ONLY with valid JSON — no markdown fences, no extra text."""

FINAL_SUMMARY_PROMPT = """Below are summaries of sequential portions of a meeting:

{chunk_summaries}

Produce structured meeting notes as a JSON object:
{{
  "overview": "2-3 sentence high-level summary",
  "key_points": ["point 1", "point 2"],
  "decisions": ["decision 1"],
  "action_items": ["Person A → Task"],
  "next_steps": ["next step 1"]
}}

Rules: key_points: 4-8 items. decisions/action_items/next_steps may be empty lists.
Return exact JSON only."""


# ── LLM call helper ───────────────────────────────────────────────────────────

def _llm(prompt: str, system: str = "", max_tokens: int = 1500,
         provider: str = "groq", model: str = "llama-3.3-70b-versatile",
         groq_key: str = None, anthropic_key: str = None,
         openai_key: str = None, together_key: str = None,
         mistral_key: str = None) -> str:
    from services.llm_clients import call_llm
    return call_llm(
        prompt=prompt, system=system, max_tokens=max_tokens,
        provider=provider, model=model,
        groq_key=groq_key, anthropic_key=anthropic_key,
        openai_key=openai_key, together_key=together_key,
        mistral_key=mistral_key,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def summarize_chunk(transcript: str, provider: str = "groq",
                    model: str = "llama-3.3-70b-versatile",
                    groq_key: str = None, anthropic_key: str = None,
                    openai_key: str = None, together_key: str = None,
                    mistral_key: str = None) -> str:
    if not transcript.strip():
        return ""
    if len(transcript) > 12000:
        transcript = transcript[:12000] + "\n[... truncated ...]"

    prompt = CHUNK_SUMMARY_PROMPT.format(transcript=transcript)
    result = _llm(prompt, system=CHUNK_SUMMARY_SYSTEM, max_tokens=600,
                  provider=provider, model=model,
                  groq_key=groq_key, anthropic_key=anthropic_key,
                  openai_key=openai_key, together_key=together_key,
                  mistral_key=mistral_key)
    return result or "[Summary unavailable for this chunk]"


def generate_final_summary(chunk_summaries: list, provider: str = "groq",
                           model: str = "llama-3.3-70b-versatile",
                           groq_key: str = None, anthropic_key: str = None,
                           openai_key: str = None, together_key: str = None,
                           mistral_key: str = None) -> dict:
    if not chunk_summaries:
        return _empty()

    valid = [s for s in chunk_summaries if s.strip()]
    if not valid:
        return _empty()

    formatted = "\n\n".join(
        f"[Segment {i+1}]\n{s}" for i, s in enumerate(valid)
    )

    kwargs = dict(provider=provider, model=model, groq_key=groq_key,
                  anthropic_key=anthropic_key, openai_key=openai_key,
                  together_key=together_key, mistral_key=mistral_key)

    if len(formatted) > 15000:
        formatted = _reduce(valid, **kwargs)

    prompt = FINAL_SUMMARY_PROMPT.format(chunk_summaries=formatted)
    raw = _llm(prompt, system=FINAL_SUMMARY_SYSTEM, max_tokens=1500, **kwargs)

    if not raw:
        return _empty()
    return _parse(raw)


def _reduce(summaries: list, **kwargs) -> str:
    groups = [summaries[i:i+5] for i in range(0, len(summaries), 5)]
    meta = []
    for i, group in enumerate(groups):
        combined = "\n".join(f"- {s}" for s in group)
        prompt = f"Summarize these {len(group)} meeting segment summaries into one paragraph:\n\n{combined}"
        result = _llm(prompt, max_tokens=400, **kwargs)
        if result:
            meta.append(f"[Group {i+1}]\n{result}")
    return "\n\n".join(meta) if meta else "\n\n".join(summaries[:10])


def _parse(raw: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        data = json.loads(text)
        return {
            "overview":     data.get("overview", ""),
            "key_points":   _list(data.get("key_points", [])),
            "decisions":    _list(data.get("decisions", [])),
            "action_items": _list(data.get("action_items", [])),
            "next_steps":   _list(data.get("next_steps", [])),
        }
    except json.JSONDecodeError:
        return {"overview": text[:500], "key_points": [],
                "decisions": [], "action_items": [], "next_steps": []}


def _list(val) -> list:
    if isinstance(val, list):
        return [str(i) for i in val if i]
    if isinstance(val, str) and val.strip():
        return [val]
    return []


def _empty() -> dict:
    return {"overview": "No content to summarize.", "key_points": [],
            "decisions": [], "action_items": [], "next_steps": []}