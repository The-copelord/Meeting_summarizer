"""
summarizer.py
LLM-based summarization using Groq API (fast inference).
Falls back to Anthropic Claude if GROQ_API_KEY not set.
Implements hierarchical chunk summarization to handle long transcripts.
"""

import os
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ─── LLM Client ──────────────────────────────────────────────────────────────

def _get_groq_client():
    """Return a Groq client if GROQ_API_KEY is available."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    try:
        from groq import Groq
        return Groq(api_key=api_key)
    except ImportError:
        logger.warning("groq package not installed")
        return None
    except Exception as e:
        logger.warning(f"Could not init Groq client: {e}")
        return None


def _call_llm(prompt: str, system: str = "", max_tokens: int = 1500) -> str:
    """
    Call an available LLM. Tries Groq first, then Anthropic.
    Returns the text response or empty string on failure.
    """
    # Try Groq
    groq_client = _get_groq_client()
    if groq_client:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            response = groq_client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"Groq call failed: {e}, trying fallback...")

    # Try Anthropic
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            full_prompt = f"{system}\n\n{prompt}" if system else prompt
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": full_prompt}],
            )
            return response.content[0].text.strip()
        except Exception as e:
            logger.warning(f"Anthropic call failed: {e}")

    logger.error("No LLM available. Set GROQ_API_KEY or ANTHROPIC_API_KEY.")
    return ""


# ─── Chunk-level summary ──────────────────────────────────────────────────────

CHUNK_SUMMARY_SYSTEM = """You are an expert meeting analyst. 
Summarize the provided meeting transcript chunk concisely and accurately.
Focus on: key topics discussed, important statements, decisions hinted at, and action items mentioned.
Be factual and preserve speaker attributions when relevant."""

CHUNK_SUMMARY_PROMPT = """Below is a portion of a meeting transcript. Please summarize the key points:

<transcript>
{transcript}
</transcript>

Provide a concise summary (3–6 sentences) of what was discussed in this portion."""


def summarize_chunk(transcript: str) -> str:
    """
    Summarize a single transcript chunk.
    
    Args:
        transcript: Speaker-labeled transcript text.
    
    Returns:
        Summary string.
    """
    if not transcript.strip():
        return ""

    # Trim very long chunks to avoid token limits (~12k chars ≈ ~3k tokens)
    max_chars = 12000
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars] + "\n[... transcript truncated for length ...]"

    prompt = CHUNK_SUMMARY_PROMPT.format(transcript=transcript)
    result = _call_llm(prompt, system=CHUNK_SUMMARY_SYSTEM, max_tokens=600)
    return result or f"[Summary unavailable for this chunk]"


# ─── Final meeting summary ────────────────────────────────────────────────────

FINAL_SUMMARY_SYSTEM = """You are a professional meeting secretary producing structured meeting notes.
Analyze the provided chunk summaries from a full meeting and extract key information.
Be precise, actionable, and well-organized.
Respond ONLY with valid JSON — no markdown fences, no extra text."""

FINAL_SUMMARY_PROMPT = """Below are summaries of sequential portions of a meeting:

{chunk_summaries}

Based on these summaries, produce structured meeting notes as a JSON object with exactly these fields:
{{
  "overview": "2-3 sentence high-level summary of the entire meeting",
  "key_points": ["point 1", "point 2", "..."],
  "decisions": ["decision 1", "decision 2", "..."],
  "action_items": ["Person A → Task description", "Person B → Task description", "..."],
  "next_steps": ["next step 1", "next step 2", "..."]
}}

Rules:
- key_points: 4-8 most important discussion topics
- decisions: concrete decisions made during the meeting (may be empty list [])
- action_items: specific tasks assigned to people (may be empty list [])
- next_steps: follow-up items mentioned (may be empty list [])
- Use exact JSON format, no markdown"""


def generate_final_summary(chunk_summaries: list[str]) -> dict:
    """
    Generate the final structured meeting summary from all chunk summaries.
    
    Args:
        chunk_summaries: List of per-chunk summary strings.
    
    Returns:
        Dict with keys: overview, key_points, decisions, action_items, next_steps
    """
    if not chunk_summaries:
        return _empty_summary()

    # Filter empty summaries
    valid_summaries = [s for s in chunk_summaries if s.strip()]
    if not valid_summaries:
        return _empty_summary()

    # Format summaries with index labels
    formatted = "\n\n".join(
        f"[Segment {i+1}]\n{summary}"
        for i, summary in enumerate(valid_summaries)
    )

    # If we have too many summaries, do a second-level hierarchy
    if len(formatted) > 15000:
        formatted = _reduce_summaries(valid_summaries)

    prompt = FINAL_SUMMARY_PROMPT.format(chunk_summaries=formatted)
    raw = _call_llm(prompt, system=FINAL_SUMMARY_SYSTEM, max_tokens=1500)

    if not raw:
        return _empty_summary()

    return _parse_summary_json(raw)


def _reduce_summaries(summaries: list[str]) -> str:
    """
    If there are too many chunk summaries, group them and summarize each group.
    This implements a second hierarchy level.
    """
    group_size = 5
    groups = [summaries[i:i+group_size] for i in range(0, len(summaries), group_size)]
    meta_summaries = []

    for i, group in enumerate(groups):
        combined = "\n".join(f"- {s}" for s in group)
        prompt = f"Summarize these {len(group)} meeting segment summaries into one concise paragraph:\n\n{combined}"
        meta = _call_llm(prompt, max_tokens=400)
        if meta:
            meta_summaries.append(f"[Group {i+1}]\n{meta}")

    return "\n\n".join(meta_summaries) if meta_summaries else "\n\n".join(summaries[:10])


def _parse_summary_json(raw: str) -> dict:
    """Parse LLM JSON output, handling common formatting issues."""
    # Strip markdown fences if present
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
        return {
            "overview": data.get("overview", ""),
            "key_points": _ensure_list(data.get("key_points", [])),
            "decisions": _ensure_list(data.get("decisions", [])),
            "action_items": _ensure_list(data.get("action_items", [])),
            "next_steps": _ensure_list(data.get("next_steps", [])),
        }
    except json.JSONDecodeError as e:
        logger.warning(f"Could not parse summary JSON: {e}. Raw: {text[:200]}")
        # Attempt to extract content from malformed JSON
        return {
            "overview": text[:500] if text else "Summary generation failed.",
            "key_points": [],
            "decisions": [],
            "action_items": [],
            "next_steps": [],
        }


def _ensure_list(value) -> list:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _empty_summary() -> dict:
    return {
        "overview": "No transcript content available to summarize.",
        "key_points": [],
        "decisions": [],
        "action_items": [],
        "next_steps": [],
    }


def get_llm_info() -> dict:
    """Return info about available LLM backends."""
    groq_available = bool(os.getenv("GROQ_API_KEY"))
    anthropic_available = bool(os.getenv("ANTHROPIC_API_KEY"))
    return {
        "groq_available": groq_available,
        "anthropic_available": anthropic_available,
        "any_available": groq_available or anthropic_available,
    }
